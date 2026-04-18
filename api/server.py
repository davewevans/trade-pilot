"""FastAPI server that reads snapshot files and serves them to the dashboard.

Runs independently from the scheduler.  Start with::

    python api/run.py          # default: localhost:8000
    uvicorn api.server:app     # alternative
"""

import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings
from database.repositories import BacktestStatsRepository, DecisionRepository, LiquidityRepository, RecommendationRepository, ScorecardRepository, TradeRepository, TokenUsageRepository

logger = logging.getLogger(__name__)


# ── Auth: password + stateless HMAC-signed tokens ───────────
# Password is mandatory. We refuse to start without one rather than
# defaulting to "" (which would silently disable the gate).
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()
if not DASHBOARD_PASSWORD:
    raise RuntimeError(
        "DASHBOARD_PASSWORD is not set. Define it in .env (local) or as a "
        "Render environment variable before starting the API."
    )

_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days
_SESSION_COOKIE = "session"

# Signing key derived from the password — stateless, survives restarts.
_SIGNING_KEY = hmac.new(
    DASHBOARD_PASSWORD.encode(),
    b"trade-pilot-session-v1",
    "sha256",
).digest()

# Paths that bypass auth entirely.
#   /auth/login   — needed to acquire a session
#   /auth/logout  — clearing your own cookie shouldn't require a valid one
#   /health, /api/health — Render health probes (and any external monitor)
_AUTH_EXEMPT_PATHS = frozenset({
    "/auth/login", "/auth/logout", "/health", "/api/health",
    "/favicon.svg", "/favicon.ico",
})


def _issue_session() -> tuple[str, float]:
    """Mint a signed token. Returns (token, expires_at_epoch).

    Token format: ``<expires_at_int>.<nonce>.<hex_signature>``
    The nonce makes each token unique even if issued at the same second.
    """
    expires_at = int(time.time()) + _SESSION_TTL_SECONDS
    nonce = secrets.token_hex(8)
    payload = f"{expires_at}.{nonce}".encode()
    sig = hmac.new(_SIGNING_KEY, payload, "sha256").hexdigest()
    return f"{expires_at}.{nonce}.{sig}", float(expires_at)


def _extract_token(request: Request) -> str | None:
    """Pull session token from cookie OR `Authorization: Bearer <token>`."""
    cookie_tok = request.cookies.get(_SESSION_COOKIE)
    if cookie_tok:
        return cookie_tok
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return None


def _is_authenticated(request: Request) -> bool:
    token = _extract_token(request)
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    expires_str, nonce, sig = parts
    try:
        expires_at = int(expires_str)
    except ValueError:
        return False
    if expires_at <= time.time():
        return False
    payload = f"{expires_str}.{nonce}".encode()
    expected = hmac.new(_SIGNING_KEY, payload, "sha256").hexdigest()
    return hmac.compare_digest(expected, sig)


# Inline login page. Self-contained: no external assets, no scripts beyond
# a single fetch() to /auth/login, includes the noindex meta required for
# all HTML this app serves.
_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>trade-pilot</title>
  <style>
    html, body { height: 100%; margin: 0; }
    body { background: #0e1116; color: #e6e6e6;
           font-family: system-ui, -apple-system, sans-serif;
           display: flex; align-items: center; justify-content: center; }
    form { background: #161b22; padding: 2rem; border-radius: 8px;
           min-width: 280px; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
    h1   { margin: 0 0 1rem; font-size: 1.05rem; font-weight: 600; }
    .logo { display: block; margin: 0 auto 1rem; width: 200px; height: 200px; max-width: 100%; }
    input[type=password] { width: 100%; padding: 0.6rem; box-sizing: border-box;
           border: 1px solid #30363d; background: #0d1117; color: #e6e6e6;
           border-radius: 4px; font-size: 0.95rem; }
    button { margin-top: 0.75rem; width: 100%; padding: 0.6rem; border: 0;
             background: #238636; color: white; border-radius: 4px;
             cursor: pointer; font-size: 0.95rem; }
    button:hover { background: #2ea043; }
    .err  { color: #f85149; font-size: 0.85rem; margin-top: 0.5rem; min-height: 1em; }
  </style>
</head>
<body>
  <form id="f" autocomplete="off">
    <img class="logo" src="/favicon.svg" alt="trade-pilot">
    <input id="p" type="password" placeholder="Password" autofocus required>
    <button type="submit">Sign in</button>
    <div id="e" class="err" role="alert"></div>
  </form>
  <script>
    document.getElementById('f').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const err = document.getElementById('e');
      err.textContent = '';
      try {
        const r = await fetch('/auth/login', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({password: document.getElementById('p').value}),
          credentials: 'same-origin',
        });
        if (r.ok) {
          window.location.href = '/';
        } else {
          const j = await r.json().catch(() => ({}));
          err.textContent = j.error || 'Invalid password';
        }
      } catch (_e) {
        err.textContent = 'Network error';
      }
    });
  </script>
</body>
</html>"""


def _login_page() -> HTMLResponse:
    return HTMLResponse(_LOGIN_HTML, status_code=200)


class AuthMiddleware(BaseHTTPMiddleware):
    """Gate every request not in _AUTH_EXEMPT_PATHS, then post-process HTML.

    Behaviour:
      * Exempt path           → pass through.
      * Authenticated         → pass through.
      * Unauthenticated /api/* → 401 JSON.
      * Unauthenticated other  → serve the inline login page (covers
        GET / per spec, plus any deep-link the user may have bookmarked).

    Post-processing for any HTML response (login page, SPA index.html
    served by StaticFiles, etc.):
      * Inject `<meta name="robots" content="noindex, nofollow">` if not
        already present.
      * Set the equivalent `X-Robots-Tag` header as a belt-and-braces.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in _AUTH_EXEMPT_PATHS or _is_authenticated(request):
            response = await call_next(request)
        elif path.startswith("/api/"):
            response = JSONResponse(
                status_code=401, content={"error": "unauthorized"},
            )
        else:
            response = _login_page()

        return await self._add_noindex(response)

    @staticmethod
    async def _add_noindex(response: Response) -> Response:
        ct = response.headers.get("content-type", "")
        if "text/html" not in ct.lower():
            return response

        # BaseHTTPMiddleware gives us a streaming response; buffer it so
        # we can inject the meta tag once. Non-streaming responses (e.g.
        # HTMLResponse / JSONResponse constructed directly in dispatch)
        # expose `.body` instead of `.body_iterator`.
        if hasattr(response, "body_iterator"):
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
        else:
            body = response.body
        if b"<head>" in body and b'name="robots"' not in body:
            body = body.replace(
                b"<head>",
                b'<head><meta name="robots" content="noindex, nofollow">',
                1,
            )
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers["x-robots-tag"] = "noindex, nofollow"
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )


# Per-leg cash flow sign convention for the trades table. Used when
# the schema's `premium_credit` is unpopulated (current state). Caller
# is responsible for filtering out NULL `fill_price` rows BEFORE this.
_SELL_TRADE_TYPES = {
    "SELL_PUT", "SELL_CALL",
    # Multi-leg spreads recorded as one row per spread. SELL_* means
    # entry is a credit (cash in). For these, fill_price is stored as
    # a positive magnitude; sign comes from the trade_type, identical
    # to the wheel convention.
    "SELL_BULL_PUT_SPREAD", "SELL_BEAR_CALL_SPREAD", "SELL_IRON_CONDOR",
    "SELL_LONG_CALL_VERTICAL",  # debit-spread CLOSE row direction
}
_BUY_TRADE_TYPES = {
    "BUY_PUT", "BUY_CALL",
    # Multi-leg debit-spread OPEN, and credit-spread CLOSE rows.
    "BUY_BULL_PUT_SPREAD", "BUY_BEAR_CALL_SPREAD", "BUY_IRON_CONDOR",
    "BUY_LONG_CALL_VERTICAL",
}


def _trade_pnl(trade: dict) -> float | None:
    """Cash-flow P&L for a single filled trade row.

    Returns None if `fill_price` is NULL (caller should have filtered)
    or if the trade_type is not one of the four recognized options.
    """
    fp = trade.get("fill_price")
    if fp is None:
        return None
    tt = (trade.get("trade_type") or "").upper()
    if tt in _SELL_TRADE_TYPES:
        sign = 1
    elif tt in _BUY_TRADE_TYPES:
        sign = -1
    else:
        return None
    contracts = int(trade.get("contracts") or 1)
    try:
        return float(fp) * contracts * 100 * sign
    except (TypeError, ValueError):
        return None


def _open_db() -> sqlite3.Connection | None:
    """Open a read-only-style sqlite3 connection or None if unavailable.

    Returns None silently if the DB file doesn't exist (so the API can
    fall back to JSONL during the dual-write transition). Logs and
    returns None on any other error.
    """
    path = Path(DB_PATH)
    if not path.exists():
        return None
    try:
        # check_same_thread=False because FastAPI may serve requests on
        # different threads; we open a fresh connection per request, so
        # there's no shared mutable state.
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        logger.exception("Failed to open SQLite DB at %s", path)
        return None


# Heartbeat paths — defined after DATA_DIR below.
# Market-hours window for heartbeat checks (America/New_York).
_HB_MARKET_OPEN_H = 6       # 6:00 AM ET
_HB_MARKET_CLOSE_H = 16     # 4:00 PM ET
_HB_MARKET_CLOSE_M = 30     # :30
_HB_STALE_MINUTES = 60      # alert if no heartbeat for this long
_HB_RESUPPRESS_MINUTES = 120  # don't re-alert within this window


async def _heartbeat_watcher() -> None:
    """Background task: check scheduler heartbeat freshness every 2 min.

    Only active during market hours (6 AM – 4:30 PM ET). Fires a "high"
    notification when the heartbeat file is stale, then suppresses
    re-alerts for 2 hours.
    """
    import asyncio
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")

    def _load_check_state() -> dict:
        try:
            if _HB_CHECK_STATE_PATH.exists():
                return json.loads(_HB_CHECK_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_check_state(state: dict) -> None:
        try:
            _HB_CHECK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _HB_CHECK_STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(_HB_CHECK_STATE_PATH))
        except Exception:
            pass

    while True:
        await asyncio.sleep(120)  # check every 2 min
        try:
            now_et = datetime.now(_ET)
            in_window = (
                now_et.weekday() < 5  # Mon–Fri
                and (
                    now_et.hour > _HB_MARKET_OPEN_H
                    or (now_et.hour == _HB_MARKET_OPEN_H)
                )
                and (
                    now_et.hour < _HB_MARKET_CLOSE_H
                    or (now_et.hour == _HB_MARKET_CLOSE_H and now_et.minute <= _HB_MARKET_CLOSE_M)
                )
            )
            if not in_window:
                continue

            if not _HEARTBEAT_PATH.exists():
                continue  # scheduler hasn't run yet; nothing to check

            try:
                hb = json.loads(_HEARTBEAT_PATH.read_text(encoding="utf-8"))
                hb_ts_str = hb.get("ts") or hb.get("timestamp") or ""
                hb_ts = datetime.fromisoformat(hb_ts_str)
                if hb_ts.tzinfo is None:
                    hb_ts = hb_ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            now_utc = datetime.now(timezone.utc)
            age_minutes = (now_utc - hb_ts).total_seconds() / 60

            if age_minutes < _HB_STALE_MINUTES:
                # Heartbeat is fresh — clear any previous alert state.
                state = _load_check_state()
                if state.get("alerted"):
                    state["alerted"] = False
                    _save_check_state(state)
                continue

            # Heartbeat is stale — check suppression window.
            state = _load_check_state()
            last_alert_str = state.get("last_alert_ts") or ""
            if last_alert_str:
                try:
                    last_alert = datetime.fromisoformat(last_alert_str)
                    if last_alert.tzinfo is None:
                        last_alert = last_alert.replace(tzinfo=timezone.utc)
                    if (now_utc - last_alert).total_seconds() / 60 < _HB_RESUPPRESS_MINUTES:
                        continue  # still within suppression window
                except Exception:
                    pass

            try:
                from notifications import notify
                notify(
                    "high",
                    "Scheduler heartbeat stale",
                    f"No heartbeat for {int(age_minutes)} min. "
                    "Scheduler may be down or stuck.",
                    tags=["heartbeat"],
                )
            except Exception:
                pass

            state["alerted"] = True
            state["last_alert_ts"] = now_utc.isoformat(timespec="seconds")
            _save_check_state(state)
            logger.warning(
                "Heartbeat stale: last seen %s min ago at %s",
                int(age_minutes), hb_ts_str,
            )
        except Exception:
            logger.debug("_heartbeat_watcher tick error", exc_info=True)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Log snapshot directory status on startup."""
    import asyncio

    logger.info("Snapshot directory: %s", SNAPSHOTS)
    for name in ("portfolio.json", "context.json", "circuit_breakers.json", "decisions.jsonl"):
        p = SNAPSHOTS / name
        if not p.exists():
            logger.warning("Snapshot file not found (will be created by scheduler): %s", p)

    # Populate per-account snapshots immediately so the dashboard has
    # data even when the API server runs separately from the scheduler
    # (e.g. on Render where they are independent services).
    try:
        from jobs.startup_snapshot import run as run_startup_snapshot
        run_startup_snapshot()
    except Exception as e:
        logger.warning("API startup snapshot failed (non-fatal): %s", e)

    # Start background heartbeat staleness checker.
    _hb_task = asyncio.create_task(_heartbeat_watcher())

    yield

    _hb_task.cancel()
    try:
        await _hb_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="trade-pilot", version="0.1.0", lifespan=lifespan)

# ── CORS ──────────────────────────────────────────────────
# This service is consumed by its own bundled SPA on the same origin —
# no third-party browser client should be able to call it. Empty
# allow_origins disables cross-origin access entirely.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth middleware is added LAST so it executes FIRST per Starlette's
# stacking order (last-added wraps the others). That way unauthenticated
# requests short-circuit before hitting any business logic.
app.add_middleware(AuthMiddleware)

# ── paths ───────────────────────────────────────────────────
SNAPSHOTS = settings.SNAPSHOTS_DIR
DATA_DIR = settings.DATA_DIR
JOURNAL_PATH = settings.JOURNAL_PATH
LOCK_PATH = DATA_DIR / "HALTED.lock"
HALT_AUDIT_PATH = DATA_DIR / "halt_audit.jsonl"
_HEARTBEAT_PATH = DATA_DIR / "heartbeat.json"
_HB_CHECK_STATE_PATH = DATA_DIR / "heartbeat_check_state.json"
DB_PATH = settings.DATABASE_PATH


# Account → strategy_type mapping. Used by the ?account= query param
# on /api/decisions, /api/decisions/stats, /api/performance.
# Omit ?account= (or pass an unknown value) for the unfiltered view.
_ACCOUNT_STRATEGY_MAP: dict[str, list[str]] = {
    "wheel": ["wheel"],
    "iron_condor": ["iron_condor"],
    "spreads": ["bull_put_spread", "bear_call_spread", "long_call_vertical"],
}


def _strategy_filter(account: str | None) -> list[str] | None:
    """Resolve ?account=... to a strategy_type list, or None for no filter."""
    if not account:
        return None
    return _ACCOUNT_STRATEGY_MAP.get(account.lower())


# ── helpers ─────────────────────────────────────────────────


def _read_json(path: Path) -> dict | None:
    """Read and parse a JSON file, returning None on any failure."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read %s", path)
        return None


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, returning a list of dicts (skipping bad lines)."""
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        logger.exception("Failed to read %s", path)
    return entries


def _read_halt_info() -> dict:
    """Parse HALTED.lock and return a halt-status dict.

    File existence is the authoritative halt signal. Contents are informational.
    Handles empty files (legacy) and the new JSON format transparently.
    """
    if not LOCK_PATH.exists():
        return {"halted": False}
    try:
        text = LOCK_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return {"halted": True, "halted_at": None, "source": "unknown", "reason": ""}
        data = json.loads(text)
        return {
            "halted": True,
            # Support old format (timestamp) and new format (halted_at)
            "halted_at": data.get("halted_at") or data.get("timestamp"),
            "source": data.get("source", "unknown"),
            "reason": data.get("reason", ""),
        }
    except Exception:
        logger.warning("Failed to parse HALTED.lock contents", exc_info=True)
        return {"halted": True, "halted_at": None, "source": "unknown", "reason": ""}


def _append_halt_audit(event: str, source: str, reason: str = "") -> None:
    """Append a halt or resume event to the audit log."""
    entry = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "reason": reason,
        "user": "dashboard",
    }
    try:
        HALT_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HALT_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        logger.exception("Failed to append halt audit log")


# ── endpoints ───────────────────────────────────────────────


@app.post("/auth/login")
async def auth_login(request: Request):
    """Validate password and issue a session cookie + JSON token."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    submitted = (body or {}).get("password", "")
    if not isinstance(submitted, str):
        return JSONResponse(status_code=400, content={"error": "password must be a string"})

    # Constant-time comparison to avoid timing oracles on the password.
    if not hmac.compare_digest(submitted.encode("utf-8"), DASHBOARD_PASSWORD.encode("utf-8")):
        return JSONResponse(status_code=401, content={"error": "invalid password"})

    token, expires_at = _issue_session()
    response = JSONResponse(
        status_code=200,
        content={"token": token, "expires_at": int(expires_at)},
    )
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        max_age=_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=bool(getattr(settings, "RENDER", False)),
        path="/",
    )
    return response


@app.get("/auth/logout")
def auth_logout(request: Request):
    """Clear the session cookie. Token validity is not server-side checked."""
    response = JSONResponse(status_code=200, content={"ok": True})
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return response


@app.get("/health")
def health_public():
    """Public health probe for Render (and any external monitor).

    Mirrors /api/health but lives at /health to match the conventional
    Render health-check path. Both are exempt from auth.
    """
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "halted": LOCK_PATH.exists(),
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        # Timezone-aware ISO string so the browser can convert to local time.
        # `datetime.now()` (naive) gets interpreted as local time by JS, which
        # produces the wrong wall-clock when the server runs in UTC (Render).
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "halted": LOCK_PATH.exists(),
        # Surfaced here so the dashboard's existing 30s health poll
        # populates the version badge without needing a separate fetch.
        "version": settings.VERSION,
        "version_date": settings.VERSION_DATE,
        # Feature flags — read by the frontend on each 30s health poll.
        "strategy_health_enabled": settings.STRATEGY_HEALTH_PAGE_ENABLED,
    }


@app.get("/api/halt-status")
def halt_status():
    """Return current halt state with metadata from HALTED.lock."""
    return _read_halt_info()


@app.post("/api/halt")
async def halt_bot(request: Request):
    """Write HALTED.lock and log the event. Returns 409 if already halted."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip()

    if LOCK_PATH.exists():
        return JSONResponse(status_code=409, content=_read_halt_info())

    payload = json.dumps({
        "halted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "dashboard",
        "reason": reason,
    }, indent=2)
    tmp_path = LOCK_PATH.with_name("HALTED.lock.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(str(tmp_path), str(LOCK_PATH))

    _append_halt_audit("halt", "dashboard", reason)
    logger.warning("Bot halted via dashboard. Reason: %r", reason)
    try:
        from notifications import notify
        notify(
            "high",
            "Bot HALTED via dashboard",
            reason or "No reason given",
            tags=["halt"],
        )
    except Exception:
        pass
    return JSONResponse(content=_read_halt_info())


@app.post("/api/resume")
def resume_bot():
    """Delete HALTED.lock and log the event. Idempotent if already running."""
    # Capture reason before deleting the lock.
    _halt_info = _read_halt_info()
    _append_halt_audit("resume", "dashboard")
    if LOCK_PATH.exists():
        LOCK_PATH.unlink()
        logger.info("Bot resumed via dashboard.")
    try:
        from notifications import notify
        _reason = _halt_info.get("reason") or ""
        _msg = f"Reason for halt was: {_reason}" if _reason else "No halt reason recorded."
        notify("high", "Bot RESUMED via dashboard", _msg, tags=["resume"])
    except Exception:
        pass
    return {"halted": False}


_CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


@app.get("/changelog")
def changelog():
    """Return CHANGELOG.md as plain text. Auth-gated by the middleware."""
    try:
        text = _CHANGELOG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return PlainTextResponse(
            "CHANGELOG.md not found on server.",
            status_code=404,
        )
    return PlainTextResponse(text)


@app.get("/api/usage")
def api_usage():
    """Return current ORATS quota usage from the last written snapshot."""
    data = _read_json(SNAPSHOTS / "api_usage.json")
    if data is None:
        return JSONResponse(status_code=503, content={"error": "No API usage snapshot yet"})
    return data


@app.get("/api/schedule")
def api_schedule():
    """Return the bot's job schedule from the canonical SCHEDULE constant in config.py."""
    from config import SCHEDULE
    return SCHEDULE


@app.get("/api/portfolio")
def portfolio():
    data = _read_json(SNAPSHOTS / "portfolio.json")
    if data is None:
        return JSONResponse(status_code=503, content={"error": "No portfolio data yet"})
    return data


@app.get("/api/context")
def context():
    data = _read_json(SNAPSHOTS / "context.json")
    if data is None:
        return JSONResponse(status_code=503, content={"error": "No context data yet"})
    return data


@app.get("/api/circuit-breakers")
def circuit_breakers():
    data = _read_json(SNAPSHOTS / "circuit_breakers.json")
    if data is None:
        return JSONResponse(status_code=503, content={"error": "No circuit breaker data yet"})
    # Override the stale on-disk dry_run flag with the live env var so the
    # dashboard reflects a DRY_RUN change immediately after redeploy, without
    # waiting for the scheduler to write a fresh snapshot.
    data["dry_run"] = os.environ.get("DRY_RUN", "false").lower() == "true"
    return data


@app.get("/api/source-health")
def source_health():
    path = SNAPSHOTS / "source_health.json"
    if not path.exists():
        return {"sources": {}}
    return {"sources": json.loads(path.read_text(encoding="utf-8"))}


_ORATS_DISABLED_PATH = DATA_DIR / "ORATS_DISABLED.lock"


@app.post("/api/admin/orats/disable")
async def admin_orats_disable(request: Request):
    """Activate the ORATS kill switch — all ORATS calls will raise OratsDisabled."""
    try:
        body = await request.json()
        reason = (body or {}).get("reason", "") if isinstance(body, dict) else ""
    except Exception:
        reason = ""
    from data.api_ledger import disable_orats
    disable_orats(reason)
    return {"status": "disabled", "reason": reason or "no reason given"}


@app.post("/api/admin/orats/enable")
def admin_orats_enable():
    """Deactivate the ORATS kill switch — ORATS calls resume normally."""
    from data.api_ledger import enable_orats
    enable_orats()
    return {"status": "enabled"}


@app.get("/api/admin/orats/status")
def admin_orats_status():
    """Return the current ORATS kill-switch state."""
    disabled = _ORATS_DISABLED_PATH.exists()
    reason = ""
    if disabled:
        try:
            reason = _ORATS_DISABLED_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return {"disabled": disabled, "reason": reason}


@app.post("/api/admin/reset-circuit-breaker")
async def reset_circuit_breaker():
    """Reset the circuit breaker — delete lock file and state."""
    deleted = []
    if LOCK_PATH.exists():
        LOCK_PATH.unlink()
        deleted.append("HALTED.lock")
    state_path = SNAPSHOTS / "circuit_breaker_state.json"
    if state_path.exists():
        state_path.unlink()
        deleted.append("circuit_breaker_state.json")
    return {"status": "reset", "deleted": deleted}


@app.get("/api/portfolio/{account_name}")
def portfolio_by_account(account_name: str):
    """Return portfolio snapshot for a specific account.

    account_name must be one of: wheel, iron_condor, spreads.
    """
    valid = {"wheel", "iron_condor", "spreads"}
    if account_name not in valid:
        return JSONResponse(
            status_code=404,
            content={"error": f"Unknown account: {account_name}"},
        )
    data = _read_json(SNAPSHOTS / f"portfolio_{account_name}.json")
    if data is None:
        return JSONResponse(
            status_code=503,
            content={"error": f"No data yet for account: {account_name}"},
        )
    return data


@app.get("/api/portfolio-greeks")
def portfolio_greeks(account: str | None = Query(default=None)):
    """Aggregate Greek exposure across open positions.

    account: optional account_id (e.g. "wheel", "spreads").  Omit for
    portfolio-wide aggregation from the primary account snapshot.

    Returns the output of compute_portfolio_greeks() which reads from
    the most recent portfolio_refresh snapshot file — no live API calls.
    """
    try:
        from data.context_builder import compute_portfolio_greeks
        return compute_portfolio_greeks(account_id=account or None)
    except Exception:
        logger.exception("portfolio-greeks failed")
        return JSONResponse(status_code=500, content={"error": "aggregation failed"})


@app.get("/api/account-portfolios")
def all_account_portfolios():
    """Return a summary of all accounts for the dashboard cards.

    Returns a dict keyed by account name. Missing accounts return
    null for that key — the frontend handles the empty state.

    (Renamed from /api/accounts to avoid conflict with account management API.)
    """
    result = {}
    for name in ("wheel", "iron_condor", "spreads"):
        data = _read_json(SNAPSHOTS / f"portfolio_{name}.json")
        result[name] = data  # None if missing — frontend shows "—"
    return result


# ── Account Management API ───────────────────────────────────────────────────


def _get_account_manager():
    from data.account_manager import AccountManager
    return AccountManager()


def _account_config_entry(account_id: str, account: dict) -> dict:
    """Build the response shape for a single account."""
    from strategies.strategy_loader import load_strategy

    strategy_name = account.get("strategy") or ""
    strategy_display = None
    if strategy_name:
        try:
            defn = load_strategy(strategy_name)
            strategy_display = defn.get("display_name", strategy_name)
        except Exception:
            strategy_display = strategy_name

    # Check credentials without exposing values
    key_var = account.get("credentials_key", "")
    secret_var = account.get("credentials_secret", "")
    has_credentials = bool(os.getenv(key_var)) and bool(os.getenv(secret_var))

    return {
        "account_id": account_id,
        "label": account.get("label", account_id),
        "strategy": strategy_name or None,
        "strategy_display_name": strategy_display,
        "status": account.get("status", "inactive"),
        "watchlist": account.get("watchlist", []),
        "screening_overrides": account.get("screening_overrides", {}),
        "has_credentials": has_credentials,
    }


@app.get("/api/accounts")
def get_accounts():
    """Return all accounts with config + list of available strategies."""
    from strategies.strategy_loader import load_all_strategies

    manager = _get_account_manager()
    all_accounts = manager.get_all_accounts()

    account_list = [
        _account_config_entry(aid, acct)
        for aid, acct in all_accounts.items()
    ]

    try:
        available = [
            {"name": defn["name"], "display_name": defn["display_name"]}
            for defn in load_all_strategies().values()
            if not defn.get("composite")
        ]
    except Exception:
        available = []

    return {"accounts": account_list, "available_strategies": available}


@app.post("/api/accounts/{account_id}/activate")
def activate_account(account_id: str):
    """Activate an account. Returns 400 if no strategy linked, 404 if not found."""
    manager = _get_account_manager()
    if manager.get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"error": f"Account {account_id!r} not found"})
    success = manager.activate(account_id)
    if not success:
        return JSONResponse(
            status_code=400,
            content={"error": f"Cannot activate account {account_id!r}: no strategy linked"},
        )
    return {"account_id": account_id, "status": "active"}


@app.post("/api/accounts/{account_id}/deactivate")
def deactivate_account(account_id: str):
    """Deactivate an account. Returns 404 if not found."""
    manager = _get_account_manager()
    if manager.get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"error": f"Account {account_id!r} not found"})
    manager.deactivate(account_id)
    return {"account_id": account_id, "status": "inactive"}


@app.post("/api/accounts/{account_id}/link-strategy")
async def link_strategy(account_id: str, request: Request):
    """Link a strategy to an account (one-time operation)."""
    manager = _get_account_manager()
    if manager.get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"error": f"Account {account_id!r} not found"})

    try:
        body = await request.json()
        strategy_name = body.get("strategy", "")
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    if not strategy_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'strategy' field"})

    ok, err = manager.link_strategy(account_id, strategy_name)
    if not ok:
        return JSONResponse(status_code=400, content={"error": err})

    return {"account_id": account_id, "strategy": strategy_name}


@app.get("/api/accounts/{account_id}/screening-filters")
def get_screening_filters(account_id: str):
    """Return screening filters for an account (stub — full implementation in Phase 2)."""
    manager = _get_account_manager()
    if manager.get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"error": f"Account {account_id!r} not found"})
    # TODO: Full implementation in Prompt 2.4 (ScreeningFilters.get_all_effective)
    return {"filters": {}}


@app.put("/api/accounts/{account_id}/screening-filters")
async def update_screening_filters(account_id: str, request: Request):
    """Update screening filter overrides for an account (stub — full implementation in Phase 2)."""
    manager = _get_account_manager()
    if manager.get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"error": f"Account {account_id!r} not found"})
    # TODO: Full implementation in Prompt 2.4 (ScreeningFilters.validate_overrides)
    return {"filters": {}}


@app.put("/api/accounts/{account_id}/watchlist")
async def update_watchlist(account_id: str, request: Request):
    """Update per-account watchlist. Body: {\"watchlist\": [\"AAPL\", \"MSFT\"]}."""
    manager = _get_account_manager()
    if manager.get_account(account_id) is None:
        return JSONResponse(status_code=404, content={"error": f"Account {account_id!r} not found"})

    try:
        body = await request.json()
        watchlist = body.get("watchlist", [])
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    if not isinstance(watchlist, list):
        return JSONResponse(status_code=400, content={"error": "'watchlist' must be a list"})

    manager.update_watchlist(account_id, watchlist)
    account = manager.get_account(account_id)
    return {"account_id": account_id, "watchlist": account.get("watchlist", [])}


@app.get("/api/equity-history")
def equity_history():
    """Equity curve data — 3 months, daily resolution."""
    data = _read_json(SNAPSHOTS / "equity_history.json")
    if data is None:
        return JSONResponse(
            status_code=503,
            content={"error": "No equity history data yet"},
        )
    return data


@app.get("/api/decisions")
def decisions(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    underlying: str | None = Query(default=None),
    action: str | None = Query(default=None),
    account: str | None = Query(default=None),
    confidence: float | None = Query(default=None),
):
    conn = _open_db()
    if conn is not None:
        try:
            repo = DecisionRepository(conn)
            rows, total = repo.query(
                limit=limit,
                offset=offset,
                underlying=underlying,
                action=action,
                strategy_types=_strategy_filter(account),
                confidence=confidence,
            )
            return {"decisions": rows, "total": total}
        finally:
            conn.close()

    # TODO: remove fallback once DB is stable
    logger.warning("DB unavailable for /api/decisions — falling back to JSONL")
    all_decisions = _read_jsonl(SNAPSHOTS / "decisions.jsonl")
    if underlying:
        ul = underlying.upper()
        all_decisions = [d for d in all_decisions if d.get("underlying", "").upper() == ul]
    if action:
        act = action.upper()
        all_decisions = [
            d for d in all_decisions
            if d.get("action", "").upper() == act
        ]

    total = len(all_decisions)
    recent = list(reversed(all_decisions))[:limit]
    return {"decisions": recent, "total": total}


@app.get("/api/decisions/stats")
def decisions_stats(account: str | None = Query(default=None)):
    conn = _open_db()
    if conn is not None:
        try:
            return _decisions_stats_from_db(conn, _strategy_filter(account))
        finally:
            conn.close()

    # TODO: remove fallback once DB is stable
    logger.warning("DB unavailable for /api/decisions/stats — falling back to JSONL")
    return _decisions_stats_from_jsonl()


def _decisions_stats_from_db(
    conn: sqlite3.Connection,
    strategy_types: list[str] | None = None,
) -> dict:
    """Aggregate stats via SQL over the decisions + trades tables."""
    where = ""
    params: list = []
    if strategy_types:
        placeholders = ",".join(["?"] * len(strategy_types))
        where = f" WHERE strategy_type IN ({placeholders})"
        params = list(strategy_types)

    total = conn.execute(
        f"SELECT COUNT(*) FROM decisions{where}", params,
    ).fetchone()[0]

    # Per-action counts. Action vocabulary is uppercased on write.
    action_counts: dict[str, int] = {}
    for row in conn.execute(
        f"SELECT action, COUNT(*) AS n FROM decisions{where} GROUP BY action",
        params,
    ):
        action_counts[str(row["action"]).upper()] = int(row["n"])

    skips = action_counts.get("SKIP", 0)
    closes = action_counts.get("CLOSE", 0)
    rolls = action_counts.get("ROLL", 0)
    # "trades" = any actionable decision (everything except HOLD/SKIP)
    holds = action_counts.get("HOLD", 0)
    trades = total - skips - holds

    # Decisions per underlying.
    by_underlying: dict[str, int] = {}
    for row in conn.execute(
        f"SELECT underlying, COUNT(*) AS n FROM decisions{where} GROUP BY underlying",
        params,
    ):
        by_underlying[row["underlying"]] = int(row["n"])

    # Skip reasons. The decisions table has no normalized skip_reason
    # column; we use the `reasoning` text directly. Cardinality may be
    # high until we normalize. (Per design note 1(a).)
    # TODO: add a skip_reason column or normalize at write time.
    skip_reasons: Counter = Counter()
    skip_where = (
        f"{where} AND UPPER(action) = 'SKIP'"
        if where else " WHERE UPPER(action) = 'SKIP'"
    )
    for row in conn.execute(
        f"SELECT reasoning FROM decisions{skip_where}", params,
    ):
        reason = (row["reasoning"] or "").strip() or "unknown"
        skip_reasons[reason] += 1

    # avg_iv_rank_at_entry: no dedicated column on decisions, and the
    # recorder doesn't currently populate trades.iv_rank_at_entry from
    # market_open. Return null until that's wired up.
    # TODO: populate trades.iv_rank_at_entry in TradeRecorder.record_trade
    avg_ivr = None

    # Win rate over filled trades (cash-flow P&L per design note 2(b)).
    repo = TradeRepository(conn)
    filled = repo.get_filled(strategy_types=strategy_types)
    closed_count = 0
    profitable = 0
    for t in filled:
        pnl = _trade_pnl(t)
        if pnl is None:
            continue
        closed_count += 1
        if pnl > 0:
            profitable += 1
    win_rate = (profitable / closed_count * 100) if closed_count else 0.0

    return {
        "total_decisions": int(total),
        "skips": skips,
        "trades": trades,
        "closes": closes,
        "rolls": rolls,
        "skip_reasons": dict(skip_reasons.most_common()),
        "win_rate": round(win_rate, 1),
        "avg_iv_rank_at_entry": avg_ivr,
        "decisions_by_underlying": by_underlying,
    }


def _decisions_stats_from_jsonl() -> dict:
    """JSONL fallback — same logic as the pre-DB implementation."""
    all_decisions = _read_jsonl(SNAPSHOTS / "decisions.jsonl")

    total = len(all_decisions)
    skips = 0
    trades = 0
    closes = 0
    rolls = 0
    skip_reasons: Counter = Counter()
    iv_ranks: list[float] = []
    by_underlying: Counter = Counter()
    profitable = 0
    closed_count = 0

    for d in all_decisions:
        act = d.get("action", "").lower()
        ul = d.get("underlying", "unknown")
        by_underlying[ul] += 1

        if act == "skip" or not d.get("action_taken"):
            skips += 1
            reason = (d.get("key_inputs") or {}).get("skip_reason") or d.get("guardrail_rejection") or "unknown"
            skip_reasons[reason] += 1
        elif act == "close":
            closes += 1
            trades += 1
        elif act == "roll":
            rolls += 1
            trades += 1
        else:
            trades += 1

        ivr = (d.get("key_inputs") or {}).get("iv_rank")
        if ivr is not None:
            try:
                iv_ranks.append(float(ivr))
            except (TypeError, ValueError):
                pass

    journal_entries = _read_jsonl(JOURNAL_PATH)
    for e in journal_entries:
        if e.get("closed_at") and e.get("pnl") is not None:
            closed_count += 1
            if float(e.get("pnl", 0)) > 0:
                profitable += 1

    win_rate = (profitable / closed_count * 100) if closed_count else 0.0
    avg_ivr = sum(iv_ranks) / len(iv_ranks) if iv_ranks else None

    return {
        "total_decisions": total,
        "skips": skips,
        "trades": trades,
        "closes": closes,
        "rolls": rolls,
        "skip_reasons": dict(skip_reasons.most_common()),
        "win_rate": round(win_rate, 1),
        "avg_iv_rank_at_entry": round(avg_ivr, 1) if avg_ivr is not None else None,
        "decisions_by_underlying": dict(by_underlying),
    }


@app.get("/api/performance")
def performance(account: str | None = Query(default=None)):
    conn = _open_db()
    if conn is not None:
        try:
            return _performance_from_db(conn, _strategy_filter(account))
        finally:
            conn.close()

    # TODO: remove fallback once DB is stable
    logger.warning("DB unavailable for /api/performance — falling back to JSONL")
    return _performance_from_jsonl()


def _performance_from_db(
    conn: sqlite3.Connection,
    strategy_types: list[str] | None = None,
) -> dict:
    """Performance roll-up from the trades table.

    Per-trade P&L is computed as `fill_price * contracts * 100 * sign`
    where SELL_* trades are +1 and BUY_* trades are -1. This matches
    the cash-flow accounting the JSONL implementation also did. Rows
    with NULL fill_price are excluded from all P&L calculations.
    """
    repo = TradeRepository(conn)
    filled = repo.get_filled(strategy_types=strategy_types)

    if not filled:
        return {
            "equity_curve": [],
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "best_trade": None,
            "worst_trade": None,
        }

    # Aggregate by date for the equity curve.
    daily_pnl: dict[str, float] = {}
    total_pnl = 0.0
    best_trade: dict | None = None
    worst_trade: dict | None = None
    best_pnl = float("-inf")
    worst_pnl = float("inf")

    for t in filled:
        pnl = _trade_pnl(t)
        if pnl is None:
            continue
        total_pnl += pnl

        # Use filled_at if present (post-reconciler), else submitted_at.
        ts = t.get("filled_at") or t.get("submitted_at") or ""
        date_key = str(ts)[:10] if ts else "unknown"
        daily_pnl[date_key] = daily_pnl.get(date_key, 0.0) + pnl

        if pnl > best_pnl:
            best_pnl = pnl
            best_trade = {**t, "pnl": round(pnl, 2)}
        if pnl < worst_pnl:
            worst_pnl = pnl
            worst_trade = {**t, "pnl": round(pnl, 2)}

    # Cumulative equity curve, ordered by date.
    equity_curve: list[dict] = []
    cum = 0.0
    for date_key in sorted(daily_pnl):
        cum += daily_pnl[date_key]
        equity_curve.append({
            "date": date_key,
            "cumulative_pnl": round(cum, 2),
        })

    portfolio_data = _read_json(SNAPSHOTS / "portfolio.json")
    portfolio_value = 0.0
    if portfolio_data:
        portfolio_value = float((portfolio_data.get("account") or {}).get("total_equity", 0))
    total_pnl_pct = (total_pnl / portfolio_value * 100) if portfolio_value else 0.0

    return {
        "equity_curve": equity_curve,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "realized_pnl": round(total_pnl, 2),
        "unrealized_pnl": 0.0,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
    }


def _performance_from_jsonl() -> dict:
    """JSONL fallback — same logic as the pre-DB implementation."""
    entries = _read_jsonl(JOURNAL_PATH)

    equity_curve: list[dict] = []
    total_pnl = 0.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    best_trade: dict | None = None
    worst_trade: dict | None = None
    best_pnl = float("-inf")
    worst_pnl = float("inf")

    for e in entries:
        pnl = e.get("pnl")
        if pnl is not None:
            pnl_f = float(pnl)
            total_pnl += pnl_f

            if e.get("closed_at"):
                realized_pnl += pnl_f
            else:
                unrealized_pnl += pnl_f

            if pnl_f > best_pnl:
                best_pnl = pnl_f
                best_trade = {
                    "symbol": e.get("symbol", "?"),
                    "underlying": e.get("underlying", "?"),
                    "action": e.get("action", "?"),
                    "pnl": pnl_f,
                    "timestamp": e.get("timestamp"),
                }
            if pnl_f < worst_pnl:
                worst_pnl = pnl_f
                worst_trade = {
                    "symbol": e.get("symbol", "?"),
                    "underlying": e.get("underlying", "?"),
                    "action": e.get("action", "?"),
                    "pnl": pnl_f,
                    "timestamp": e.get("timestamp"),
                }

        ts = e.get("timestamp")
        if ts and e.get("fill_price") is not None:
            equity_curve.append({"timestamp": ts, "equity": round(total_pnl, 2)})

    portfolio_data = _read_json(SNAPSHOTS / "portfolio.json")
    portfolio_value = 0.0
    if portfolio_data:
        portfolio_value = float((portfolio_data.get("account") or {}).get("total_equity", 0))

    total_pnl_pct = (total_pnl / portfolio_value * 100) if portfolio_value else 0.0

    return {
        "equity_curve": equity_curve,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
    }


@app.get("/api/trades")
def trades(
    account: str | None = Query(default=None),
    underlying: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    conn = _open_db()
    if conn is not None:
        try:
            repo = TradeRepository(conn)
            rows = repo.get_filled(
                strategy_types=_strategy_filter(account),
                underlying=underlying,
            )
            # Most recent first; attach computed pnl per the same sign
            # convention used by /api/performance.
            rows.reverse()
            enriched = []
            for r in rows:
                pnl = _trade_pnl(r)
                enriched.append({**r, "pnl": round(pnl, 2) if pnl is not None else None})
            total = len(enriched)
            return {"trades": enriched[:limit], "total": total}
        finally:
            conn.close()

    # TODO: remove fallback once DB is stable
    logger.warning("DB unavailable for /api/trades — falling back to JSONL")
    return _trades_from_jsonl(account=account, underlying=underlying, limit=limit)


def _trades_from_jsonl(
    account: str | None,
    underlying: str | None,
    limit: int,
) -> dict:
    """JSONL fallback. Filters journal.jsonl entries with a fill_price."""
    entries = _read_jsonl(JOURNAL_PATH)
    strategy_types = _strategy_filter(account)
    out: list[dict] = []
    for e in entries:
        if e.get("fill_price") is None:
            continue
        st = e.get("strategy_type") or ("wheel" if e.get("wheel_state") else None)
        if strategy_types and st not in strategy_types:
            continue
        if underlying and (e.get("underlying") or "").upper() != underlying.upper():
            continue
        out.append({
            "filled_at": e.get("closed_at") or e.get("timestamp"),
            "submitted_at": e.get("timestamp"),
            "underlying": e.get("underlying"),
            "strategy_type": st,
            "trade_type": (e.get("action") or "").upper(),
            "symbol": e.get("contract_symbol") or e.get("symbol"),
            "fill_price": e.get("fill_price"),
            "contracts": e.get("qty") or 1,
            "pnl": e.get("pnl"),
        })
    out.reverse()
    return {"trades": out[:limit], "total": len(out)}


@app.get("/api/strategy-states")
def strategy_states():
    portfolio = _read_json(SNAPSHOTS / "portfolio.json")

    # Read individual strategy state files
    _STRATEGY_FILES = {
        "iron_condor": "iron_condor_state.json",
        "bull_put_spread": "bull_put_spread_state.json",
        "bear_call_spread": "bear_call_spread_state.json",
        "long_call_vertical": "long_call_vertical_state.json",
    }

    states: dict = {}
    for name, filename in _STRATEGY_FILES.items():
        data = _read_json(SNAPSHOTS / filename)
        if data:
            states[name] = {
                "state": data.get("state", "IDLE"),
                "spread_id": data.get("open_spread_id"),
            }
        else:
            states[name] = {"state": "IDLE", "spread_id": None}

    # Wheel state from portfolio snapshot
    wheel_states = {}
    if portfolio:
        wheel_states = portfolio.get("wheel_states", {})
    states["wheel"] = {"state": wheel_states or "IDLE", "underlying": None}

    # Active strategies from last context (if router info is available)
    context = _read_json(SNAPSHOTS / "context.json")
    regime = (context or {}).get("confirmed_market_regime", "NEUTRAL")
    cb = _read_json(SNAPSHOTS / "circuit_breakers.json") or {}
    cb_status = cb.get("status", "GREEN")

    router_blocked = cb_status in ("RED", "YELLOW") or regime == "CRASH"
    block_reason = None
    if cb_status == "RED":
        block_reason = "Circuit breaker RED"
    elif cb_status == "YELLOW":
        block_reason = "Circuit breaker YELLOW — management only"
    elif regime == "CRASH":
        block_reason = "CRASH regime — wheel only"

    return {
        **states,
        "router_blocked": router_blocked,
        "block_reason": block_reason,
    }


@app.get("/api/regime-history")
def regime_history():
    data = _read_json(SNAPSHOTS / "regime_history.json")
    if data is None:
        return {"readings": [], "confirmed": "NEUTRAL"}
    return data


@app.get("/api/watchlist")
def get_watchlist():
    """Return the current wheel, iron_condor, and spreads watchlists."""
    path = DATA_DIR / "watchlist.json"
    if path.exists():
        data = _read_json(path)
        if data is not None:
            return data
    return {
        "wheel": list(settings.WATCHLIST),
        "iron_condor": list(settings.IRON_CONDOR_WATCHLIST),
        "spreads": list(settings.SPREAD_WATCHLIST),
        "updated_at": None,
    }


# ── Backtest job store ───────────────────────────────────────────────────────
# ── IV history in-memory cache (24h TTL) ─────────────────────────────────────
# ORATS hist/ivrank is already persisted in SQLite (immutable historical data).
# This layer avoids even the SQLite lookup on repeated dashboard refreshes.
_IV_HISTORY_CACHE: dict[str, tuple[float, dict]] = {}
_IV_HISTORY_CACHE_TTL = 24 * 60 * 60  # seconds


@app.get("/api/iv-history")
def iv_history(
    symbol: str = Query(..., min_length=1, max_length=10),
    days: int = Query(default=365, ge=30, le=1095),
):
    """Daily IV rank history for a symbol, plus trade entry markers.

    Response shape::

        {
          "symbol": "AAPL",
          "days": 365,
          "iv_history": [
            {"date": "2024-01-02", "iv_rank_1y": 45.2, "iv_rank_1m": 38.1, "iv": 0.22},
            ...
          ],
          "trade_markers": [
            {"date": "2024-03-15", "iv_rank": 62.1, "strategy_type": "bull_put_spread", "trade_type": "sell_to_open"},
            ...
          ]
        }
    """
    sym = symbol.upper()
    cache_key = f"{sym}:{days}"
    now = time.monotonic()

    cached = _IV_HISTORY_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _IV_HISTORY_CACHE_TTL:
        return cached[1]

    end_dt = date.today()
    start_dt = end_dt - timedelta(days=days)

    try:
        from data.orats_historical import ORATSHistorical
        raw_rows = ORATSHistorical().get_iv_rank_history(
            sym, start_dt.isoformat(), end_dt.isoformat(),
        )
    except Exception:
        logger.exception("IV history fetch failed for %s", sym)
        return JSONResponse(status_code=503, content={"error": "IV history unavailable"})

    # Normalise raw ORATS camelCase fields to friendly snake_case for the frontend.
    iv_history_points = []
    for row in raw_rows:
        trade_date = row.get("tradeDate") or row.get("trade_date") or ""
        iv_rank_1y = row.get("ivRank1y") or row.get("iv_rank_1y")
        iv_rank_1m = row.get("ivRank1m") or row.get("iv_rank_1m")
        iv_raw = row.get("iv")
        if not trade_date:
            continue
        iv_history_points.append({
            "date": str(trade_date)[:10],
            "iv_rank_1y": round(float(iv_rank_1y), 1) if iv_rank_1y is not None else None,
            "iv_rank_1m": round(float(iv_rank_1m), 1) if iv_rank_1m is not None else None,
            "iv": round(float(iv_raw) * 100, 2) if iv_raw is not None else None,  # as pct
        })

    iv_history_points.sort(key=lambda x: x["date"])

    # ── Trade entry markers ──────────────────────────────────────────────────
    trade_markers: list[dict] = []
    conn = _open_db()
    if conn is not None:
        try:
            repo = TradeRepository(conn)
            filled = repo.get_filled(underlying=sym)
            # Only show sell-to-open trades (entries, not exits)
            for t in filled:
                if t.get("trade_type") not in ("sell_to_open", "buy_to_open"):
                    continue
                filled_date = (t.get("filled_at") or "")[:10]
                if not filled_date or filled_date < start_dt.isoformat():
                    continue
                trade_markers.append({
                    "date": filled_date,
                    "iv_rank": round(float(t["iv_rank_at_entry"]), 1)
                    if t.get("iv_rank_at_entry") is not None else None,
                    "strategy_type": t.get("strategy_type"),
                    "trade_type": t.get("trade_type"),
                })
        except Exception:
            logger.warning("Trade marker fetch failed for %s", sym)
        finally:
            conn.close()

    result = {
        "symbol": sym,
        "days": days,
        "iv_history": iv_history_points,
        "trade_markers": trade_markers,
    }
    _IV_HISTORY_CACHE[cache_key] = (now, result)
    return result


# Jobs run in background threads. Results are stored in memory until retrieved.
# Keys: job_id (str) → dict with status/progress/result fields.

_BACKTEST_JOBS: dict[str, dict] = {}
_BACKTEST_JOBS_LOCK = threading.Lock()

# Single-flight concurrency control for the interactive backtest endpoint.
# Only one backtest may run at a time to prevent accidental ORATS budget exhaustion.
_api_backtest_lock_holder = None   # ProcessLock instance when a backtest is running
_api_backtest_started_at: str | None = None  # ISO timestamp when the backtest started
_api_backtest_ctrl_lock = threading.Lock()  # guards the two variables above


def _run_backtest_job(job_id: str, params_dict: dict) -> None:
    """Background thread target: run the backtest and store result."""
    from backtesting.engine import BacktestEngine, BacktestParams
    from data.api_ledger import current_job_source
    from dataclasses import asdict

    log_lines: list[str] = []

    def progress(msg: str) -> None:
        log_lines.append(msg)
        with _BACKTEST_JOBS_LOCK:
            if job_id in _BACKTEST_JOBS:
                _BACKTEST_JOBS[job_id]["progress"] = msg
                _BACKTEST_JOBS[job_id]["log"] = list(log_lines)

    with _BACKTEST_JOBS_LOCK:
        _BACKTEST_JOBS[job_id]["status"] = "running"

    # Tag all ORATS ledger records in this thread as "api_backtest"
    _token = current_job_source.set("api_backtest")
    try:
        params = BacktestParams(
            strategy=params_dict.get("strategy", "bull_put_spread"),
            symbols=params_dict.get("symbols", ["SPY"]),
            start_date=params_dict.get("start_date", "2023-01-01"),
            end_date=params_dict.get("end_date", "2023-12-31"),
            delta=float(params_dict.get("delta", 0.30)),
            dte_min=int(params_dict.get("dte_min", 21)),
            dte_max=int(params_dict.get("dte_max", 45)),
            ivr_threshold=float(params_dict.get("ivr_threshold", 30.0)),
            profit_close_pct=float(params_dict.get("profit_close_pct", 0.50)),
            contracts=int(params_dict.get("contracts", 1)),
            spread_width_strikes=int(params_dict.get("spread_width_strikes", 5)),
        )

        engine = BacktestEngine()
        result = engine.run(params, progress_cb=progress)

        # Serialize trades
        trades_out = []
        for t in result.trades:
            trades_out.append(asdict(t))

        with _BACKTEST_JOBS_LOCK:
            _BACKTEST_JOBS[job_id].update({
                "status": "complete",
                "result": {
                    "params": result.params,
                    "total_pnl": result.total_pnl,
                    "win_rate": result.win_rate,
                    "avg_trade_pnl": result.avg_trade_pnl,
                    "max_drawdown": result.max_drawdown,
                    "avg_duration_days": result.avg_duration_days,
                    "total_trades": result.total_trades,
                    "winning_trades": result.winning_trades,
                    "monthly_returns": result.monthly_returns,
                    "equity_curve": result.equity_curve,
                    "trades": trades_out,
                },
            })

    except Exception as exc:
        logger.exception("Backtest job %s failed", job_id)
        with _BACKTEST_JOBS_LOCK:
            _BACKTEST_JOBS[job_id].update({
                "status": "error",
                "error": str(exc),
            })

    finally:
        # Release source attribution and process lock regardless of outcome
        current_job_source.reset(_token)
        global _api_backtest_lock_holder, _api_backtest_started_at
        with _api_backtest_ctrl_lock:
            if _api_backtest_lock_holder is not None:
                _api_backtest_lock_holder.release()
                _api_backtest_lock_holder = None
                _api_backtest_started_at = None


@app.post("/api/backtest")
async def start_backtest(request: Request):
    """Start a backtest job. Returns a job_id for polling."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    # Validate required fields
    strategy = body.get("strategy", "bull_put_spread")
    from backtesting.engine import SUPPORTED_STRATEGIES, BacktestEngine
    if strategy not in SUPPORTED_STRATEGIES:
        return JSONResponse(
            status_code=400,
            content={"error": f"strategy must be one of: {SUPPORTED_STRATEGIES}"},
        )

    symbols = body.get("symbols", [])
    if not symbols or not isinstance(symbols, list):
        return JSONResponse(status_code=400, content={"error": "symbols must be a non-empty list"})

    try:
        start_date = body["start_date"]
        end_date = body["end_date"]
        from datetime import date as _date
        _date.fromisoformat(start_date)
        _date.fromisoformat(end_date)
        if start_date >= end_date:
            raise ValueError("start_date must be before end_date")
    except (KeyError, ValueError) as e:
        return JSONResponse(status_code=400, content={"error": f"Invalid date range: {e}"})

    # ── Pre-flight ORATS budget check ─────────────────────────────────────────
    try:
        from data.api_ledger import get_ledger
        _engine_for_estimate = BacktestEngine()
        estimate = _engine_for_estimate.estimate_cost(symbols, strategy, start_date, end_date)
        _usage = get_ledger().get_usage("orats_historical")
        month_remaining = _usage["month_remaining"]

        if estimate > month_remaining:
            logger.warning(
                "Backtest pre-flight BLOCKED: estimate=%d > month_remaining=%d",
                estimate, month_remaining,
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": "budget_exceeded",
                    "estimate": estimate,
                    "remaining": month_remaining,
                    "message": (
                        f"This backtest would use ~{estimate:,} ORATS calls but only "
                        f"{month_remaining:,} remain this month."
                    ),
                },
            )

        confirm_heavy = body.get("confirm_heavy", False)
        if estimate > month_remaining * 0.7 and not confirm_heavy:
            logger.info(
                "Backtest pre-flight: confirm_required — estimate=%d, month_remaining=%d",
                estimate, month_remaining,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "confirm_required",
                    "estimate": estimate,
                    "remaining": month_remaining,
                    "message": (
                        f"This backtest would use ~{estimate:,} ORATS calls (>70% of the "
                        f"{month_remaining:,} remaining this month). "
                        "Resubmit with confirm_heavy=true to proceed."
                    ),
                },
            )
    except Exception:
        logger.warning("Backtest pre-flight check failed (non-fatal) — proceeding", exc_info=True)

    # ── Concurrency guard: one backtest at a time ─────────────────────────────
    from utils.process_lock import ProcessLock, ProcessLockHeld
    global _api_backtest_lock_holder, _api_backtest_started_at
    with _api_backtest_ctrl_lock:
        if _api_backtest_lock_holder is not None:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "backtest_already_running",
                    "since": _api_backtest_started_at,
                    "message": "A backtest is already running — please wait for it to complete.",
                },
            )
        try:
            _lock = ProcessLock("api_backtest", settings.DATA_DIR / "locks")
            _lock.acquire()
            _api_backtest_lock_holder = _lock
            _api_backtest_started_at = datetime.now(timezone.utc).isoformat()
        except ProcessLockHeld:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "backtest_already_running",
                    "since": _api_backtest_started_at,
                    "message": "A backtest is already running — please wait for it to complete.",
                },
            )

    job_id = str(uuid.uuid4())
    with _BACKTEST_JOBS_LOCK:
        _BACKTEST_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "progress": "Starting...",
            "log": [],
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    t = threading.Thread(
        target=_run_backtest_job,
        args=(job_id, body),
        daemon=True,
        name=f"backtest-{job_id[:8]}",
    )
    t.start()

    return {"job_id": job_id, "status": "queued"}


@app.get("/api/backtest/{job_id}")
def get_backtest_result(job_id: str):
    """Poll for backtest job status and results."""
    with _BACKTEST_JOBS_LOCK:
        job = _BACKTEST_JOBS.get(job_id)

    if job is None:
        return JSONResponse(status_code=404, content={"error": "job not found"})

    # Return everything except full trade list until complete (to keep response light)
    response = {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "error": job.get("error"),
        "created_at": job.get("created_at"),
    }
    if job["status"] == "complete" and job.get("result"):
        response["result"] = job["result"]

    return response


@app.post("/api/watchlist")
async def update_watchlist(request: Request):
    """Overwrite the watchlist file and hot-reload into settings."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    wheel = body.get("wheel", [])
    iron_condor = body.get("iron_condor", [])
    spreads = body.get("spreads", [])

    if not isinstance(wheel, list) or not isinstance(iron_condor, list) or not isinstance(spreads, list):
        return JSONResponse(status_code=400, content={"error": "wheel, iron_condor, and spreads must be arrays"})

    for sym in wheel + iron_condor + spreads:
        if not isinstance(sym, str) or not sym.isalpha() or not sym.isupper() or len(sym) > 6:
            return JSONResponse(status_code=400, content={"error": f"Invalid symbol: {sym!r}"})

    data = {
        "wheel": wheel,
        "iron_condor": iron_condor,
        "spreads": spreads,
        "updated_at": datetime.now().isoformat(),
    }
    path = DATA_DIR / "watchlist.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Hot-reload so the next scheduler cycle uses the new lists
    settings.WATCHLIST = wheel
    settings.IRON_CONDOR_WATCHLIST = iron_condor
    settings.SPREAD_WATCHLIST = spreads

    logger.info(
        "Watchlist updated: %d wheel, %d iron_condor, %d spreads",
        len(wheel), len(iron_condor), len(spreads),
    )
    return data


# ── Fill Quality ─────────────────────────────────────────────────────────────


def _compute_fill_quality(
    conn: sqlite3.Connection,
    strategy_types: list[str] | None,
    days: int,
) -> dict:
    """Compute per-fill slippage stats from trades that have both prices."""
    _empty = {
        "trades_analyzed": 0,
        "avg_slippage": 0.0,
        "median_slippage": 0.0,
        "total_slippage_dollars": 0.0,
        "positive_slippage_count": 0,
        "negative_slippage_count": 0,
        "exact_fill_count": 0,
        "worst_slippage": 0.0,
        "best_slippage": 0.0,
        "recent_fills": [],
    }

    params: list = []
    extra = ""
    if strategy_types:
        placeholders = ",".join(["?"] * len(strategy_types))
        extra += f" AND strategy_type IN ({placeholders})"
        params.extend(strategy_types)

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    extra += " AND filled_at >= ?"
    params.append(cutoff)

    rows = conn.execute(
        f"""
        SELECT symbol, underlying, strategy_type, limit_price,
               fill_price, filled_at
          FROM trades
         WHERE fill_status = 'filled'
           AND fill_price IS NOT NULL
           AND limit_price IS NOT NULL
           AND limit_price > 0
           {extra}
         ORDER BY filled_at DESC
        """,
        params,
    ).fetchall()
    rows = [dict(r) for r in rows]

    if len(rows) < 2:
        return _empty

    slippages: list[float] = []
    pos_count = neg_count = exact_count = 0

    for r in rows:
        try:
            fp = float(r["fill_price"])
            lp = float(r["limit_price"])
        except (TypeError, ValueError):
            continue
        slip = fp - lp
        slippages.append(slip)
        if slip > 0:
            pos_count += 1
        elif slip < 0:
            neg_count += 1
        else:
            exact_count += 1

    if not slippages:
        return _empty

    avg_slip = sum(slippages) / len(slippages)
    s = sorted(slippages)
    n = len(s)
    median_slip = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    total_dollars = sum(sl * 100 for sl in slippages)

    recent_fills = []
    for r in rows[:20]:
        try:
            fp = float(r["fill_price"])
            lp = float(r["limit_price"])
            recent_fills.append({
                "symbol": r.get("symbol", ""),
                "underlying": r.get("underlying", ""),
                "strategy_type": r.get("strategy_type", ""),
                "limit_price": round(lp, 4),
                "fill_price": round(fp, 4),
                "slippage": round(fp - lp, 4),
                "filled_at": r.get("filled_at", ""),
            })
        except (TypeError, ValueError):
            continue

    return {
        "trades_analyzed": len(slippages),
        "avg_slippage": round(avg_slip, 4),
        "median_slippage": round(median_slip, 4),
        "total_slippage_dollars": round(total_dollars, 2),
        "positive_slippage_count": pos_count,
        "negative_slippage_count": neg_count,
        "exact_fill_count": exact_count,
        "worst_slippage": round(max(slippages), 4),
        "best_slippage": round(min(slippages), 4),
        "recent_fills": recent_fills,
    }


@app.get("/api/fill-quality")
def fill_quality(
    account: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
):
    """Fill quality stats: slippage between limit_price and actual fill_price."""
    _empty = {
        "trades_analyzed": 0,
        "avg_slippage": 0.0,
        "median_slippage": 0.0,
        "total_slippage_dollars": 0.0,
        "positive_slippage_count": 0,
        "negative_slippage_count": 0,
        "exact_fill_count": 0,
        "worst_slippage": 0.0,
        "best_slippage": 0.0,
        "recent_fills": [],
    }
    conn = _open_db()
    if conn is None:
        return _empty
    try:
        return _compute_fill_quality(conn, _strategy_filter(account), days)
    except Exception:
        logger.exception("fill-quality computation failed")
        return _empty
    finally:
        conn.close()


# ── NTA Events ───────────────────────────────────────────────────────────────


def _extract_underlying_from_occ(symbol: str) -> str:
    """Extract root ticker from an OCC option symbol (leading alpha chars)."""
    root = ""
    for ch in symbol:
        if ch.isalpha():
            root += ch
        else:
            break
    return root.upper()


@app.get("/api/nta-events")
def nta_events(days: int = Query(default=7, ge=1, le=90)):
    """Recent assignment / expiry / exercise events from the wheel broker."""
    _TYPE_MAP = {
        "OPASN": "assignment",
        "OPEXP": "expiry",
        "OPEXC": "exercise",
    }
    try:
        from brokers.broker_factory import get_broker
        broker = get_broker()

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        activities = broker.get_account_activities(
            ["OPASN", "OPEXP", "OPEXC"],
            after=cutoff.isoformat(),
        )

        events = []
        for act in activities:
            raw_type = act.get("activity_type", "")
            event_type = _TYPE_MAP.get(raw_type, raw_type.lower())
            symbol = act.get("symbol", "")
            underlying = _extract_underlying_from_occ(symbol)

            price_raw = act.get("price") or act.get("per_share_amount")
            try:
                price = float(price_raw) if price_raw is not None else None
            except (TypeError, ValueError):
                price = None

            try:
                qty = int(act.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0

            date_raw = act.get("date") or act.get("transaction_time") or ""
            event_date = str(date_raw)[:10] if date_raw else ""

            events.append({
                "type": event_type,
                "symbol": symbol,
                "underlying": underlying,
                "qty": qty,
                "price": price,
                "date": event_date,
                "raw_type": raw_type,
            })

        return {"events": events, "total": len(events)}

    except Exception:
        logger.exception("Failed to fetch NTA events")
        return {"events": [], "total": 0, "error": "Failed to fetch NTA events"}


# ── Pending count ─────────────────────────────────────────────────────────────


@app.get("/api/pending-count")
def pending_count():
    """Count of trades still awaiting fill confirmation."""
    conn = _open_db()
    if conn is None:
        return {"count": 0}
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE fill_status = 'pending'"
        ).fetchone()
        return {"count": int(row[0]) if row else 0}
    except Exception:
        logger.exception("pending-count query failed")
        return {"count": 0}
    finally:
        conn.close()


# ── Research endpoints ────────────────────────────────────────────────────────


@app.get("/api/research/liquidity/scores")
def research_liquidity_scores():
    """All symbol_liquidity_scores rows, grouped by strategy_type."""
    conn = _open_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if conn is None:
        return {"generated_at": now, "strategies": {}}
    try:
        repo = LiquidityRepository(conn)
        rows = repo.get_all_scores()
        grouped: dict[str, list] = {}
        for r in rows:
            strat = r.get("strategy_type", "unknown")
            grouped.setdefault(strat, []).append(r)
        return {"generated_at": now, "strategies": grouped}
    except Exception:
        logger.exception("research/liquidity/scores query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/research/winrate/symbol-stats")
def research_winrate_symbol_stats():
    """All symbol_strategy_stats rows, grouped by strategy_type."""
    conn = _open_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if conn is None:
        return {"generated_at": now, "strategies": {}}
    try:
        repo = BacktestStatsRepository(conn)
        rows = repo.get_all_symbol_stats()
        grouped: dict[str, list] = {}
        for r in rows:
            strat = r.get("strategy_type", "unknown")
            grouped.setdefault(strat, []).append(r)
        return {"generated_at": now, "strategies": grouped}
    except Exception:
        logger.exception("research/winrate/symbol-stats query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/research/winrate/regime-stats")
def research_winrate_regime_stats():
    """All regime_strategy_stats rows, grouped by entry_regime."""
    conn = _open_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if conn is None:
        return {"generated_at": now, "regimes": {}}
    try:
        repo = BacktestStatsRepository(conn)
        rows = repo.get_all_regime_stats()
        grouped: dict[str, list] = {}
        for r in rows:
            regime = r.get("entry_regime", "unknown")
            grouped.setdefault(regime, []).append(r)
        return {"generated_at": now, "regimes": grouped}
    except Exception:
        logger.exception("research/winrate/regime-stats query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/research/winrate/symbol/{ticker}")
def research_winrate_symbol(ticker: str):
    """Per-symbol deep dive: all strategy stats + last 20 trades per strategy.

    Returns 404 if the ticker has no backtest data at all.
    """
    ticker = ticker.upper()
    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        repo = BacktestStatsRepository(conn)
        all_stats = repo.get_all_symbol_stats()
        symbol_stats = {
            r["strategy_type"]: r
            for r in all_stats
            if r.get("symbol", "").upper() == ticker
        }
        if not symbol_stats:
            return JSONResponse(
                status_code=404,
                content={"error": f"No backtest data for symbol: {ticker}"},
            )

        strategies = [
            "wheel_csp", "bull_put_spread", "bear_call_spread",
            "iron_condor", "long_call_vertical",
        ]
        recent_trades: dict[str, list] = {}
        for strat in strategies:
            trades = repo.get_trades(symbol=ticker, strategy_type=strat)
            # Return most recent 20, sorted by entry_date desc
            trades_sorted = sorted(trades, key=lambda t: t.get("entry_date", ""), reverse=True)
            recent_trades[strat] = trades_sorted[:20]

        return {
            "symbol": ticker,
            "stats": symbol_stats,
            "recent_trades": recent_trades,
        }
    except Exception:
        logger.exception("research/winrate/symbol/%s query failed", ticker)
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/research/winrate/coverage")
def research_winrate_coverage():
    """Summary coverage metrics for the win-rate research layer."""
    conn = _open_db()
    if conn is None:
        return {
            "total_backtest_trades": 0,
            "symbols_with_stats": 0,
            "high_confidence_pairs": 0,
            "low_confidence_pairs": 0,
            "no_data_pairs": 0,
            "oldest_trade": None,
            "newest_trade": None,
            "last_sweep_run": None,
        }
    try:
        repo = BacktestStatsRepository(conn)

        # Count trades
        trade_row = conn.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()
        total_trades = int(trade_row[0]) if trade_row else 0

        # Date range
        date_row = conn.execute(
            "SELECT MIN(entry_date), MAX(entry_date) FROM backtest_trades"
        ).fetchone()
        oldest_trade = date_row[0] if date_row else None
        newest_trade = date_row[1] if date_row else None

        # Stats summary
        all_stats = repo.get_all_symbol_stats()
        symbols_with_stats = len({r["symbol"] for r in all_stats})
        high_conf = sum(1 for r in all_stats if r.get("confidence") == "high")
        low_conf = sum(1 for r in all_stats if r.get("confidence") == "low")
        no_data = sum(1 for r in all_stats if r.get("confidence") == "none")

        # Last sweep run from state file
        sweep_state_path = DATA_DIR / "backtest_sweep_state.json"
        last_sweep_run = None
        if sweep_state_path.exists():
            try:
                state = json.loads(sweep_state_path.read_text(encoding="utf-8"))
                last_sweep_run = state.get("last_completed_at")
            except Exception:
                pass

        return {
            "total_backtest_trades": total_trades,
            "symbols_with_stats": symbols_with_stats,
            "high_confidence_pairs": high_conf,
            "low_confidence_pairs": low_conf,
            "no_data_pairs": no_data,
            "oldest_trade": oldest_trade,
            "newest_trade": newest_trade,
            "last_sweep_run": last_sweep_run,
        }
    except Exception:
        logger.exception("research/winrate/coverage query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


# ── Recommendation endpoints ─────────────────────────────────────────────────

_WATCHLIST_KEY = {
    "wheel": "WATCHLIST",
    "iron_condor": "IRON_CONDOR_WATCHLIST",
    "spreads": "SPREAD_WATCHLIST",
}
_WATCHLIST_MIN_MEMBERS = 5


@app.get("/api/research/recommendations")
def research_recommendations():
    """Return the latest watchlist recommendations from the snapshot file."""
    path = SNAPSHOTS / "watchlist_recommendations.json"
    data = _read_json(path)
    if data is None:
        return JSONResponse(status_code=404, content={"error": "no recommendations generated yet"})
    # Load current watchlist members so the UI can show them alongside recs
    wl_path = DATA_DIR / "watchlist.json"
    wl_data = _read_json(wl_path) or {}
    result: dict = {"generated_at": data.get("generated_at"), "watchlists": {}}
    for key in ("wheel", "iron_condor", "spreads"):
        wl_entry = data.get(key, {})
        result["watchlists"][key] = {
            "current_members": wl_data.get(key, []),
            "add": wl_entry.get("add", []),
            "remove": wl_entry.get("remove", []),
            "no_change": wl_entry.get("no_change", []),
            "considered_but_rejected": wl_entry.get("considered_but_rejected", []),
        }
    return result


@app.get("/api/research/recommendations/history")
def research_recommendations_history():
    """Return last 100 recommendation rows including operator decisions."""
    conn = _open_db()
    if conn is None:
        return {"rows": []}
    try:
        repo = RecommendationRepository(conn)
        rows = repo.get_history(limit=100)
        return {"rows": rows}
    except Exception:
        logger.exception("research/recommendations/history query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.post("/api/research/recommendations/apply")
async def research_recommendations_apply(request: Request):
    """Accept or reject pending recommendations, applying accepted ones to watchlist.json."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    accepted_items = body.get("accepted", [])
    rejected_items = body.get("rejected", [])

    if not isinstance(accepted_items, list) or not isinstance(rejected_items, list):
        return JSONResponse(status_code=400, content={"error": "'accepted' and 'rejected' must be arrays"})

    all_ids = [item.get("recommendation_id") for item in accepted_items + rejected_items]
    if not all(isinstance(i, int) for i in all_ids):
        return JSONResponse(status_code=400, content={"error": "each item must have an integer recommendation_id"})

    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})

    try:
        repo = RecommendationRepository(conn)

        # Validate: all IDs must exist and be undecided
        all_history = repo.get_history(limit=10000)
        history_by_id = {r["recommendation_id"]: r for r in all_history}

        errors: list[str] = []
        for rid in all_ids:
            if rid not in history_by_id:
                errors.append(f"recommendation_id {rid} not found")
            elif history_by_id[rid]["operator_decision"] is not None:
                errors.append(f"recommendation_id {rid} already decided as {history_by_id[rid]['operator_decision']!r}")
        if errors:
            return JSONResponse(status_code=400, content={"errors": errors})

        # Load current watchlist
        wl_path = DATA_DIR / "watchlist.json"
        wl_data = _read_json(wl_path) or {"wheel": [], "iron_condor": [], "spreads": []}
        wheel = list(wl_data.get("wheel", []))
        iron_condor = list(wl_data.get("iron_condor", []))
        spreads = list(wl_data.get("spreads", []))
        wl_map = {"wheel": wheel, "iron_condor": iron_condor, "spreads": spreads}

        # Pre-validate: check remove would not drop below minimum
        for item in accepted_items:
            rid = item["recommendation_id"]
            rec = history_by_id[rid]
            if rec["action"] == "remove":
                wl_name = rec["watchlist_name"]
                current = wl_map.get(wl_name, [])
                if len(current) - 1 < _WATCHLIST_MIN_MEMBERS:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": (
                                f"Cannot remove {rec['symbol']} from {wl_name}: "
                                f"would drop below minimum {_WATCHLIST_MIN_MEMBERS} members "
                                f"(currently {len(current)})"
                            )
                        },
                    )

        # CROSS-PROCESS WRITE TARGET: watchlist_recommendations is the only
        # table written by both the API process (operator decisions below, via
        # repo.record_decision()) and the scheduler process (weekly_research.py
        # batch inserts via insert_batch()). @db_retry() on both write methods
        # handles SQLite WAL lock contention between the two writers. If you add
        # new cross-process write paths, document them here and confirm
        # @db_retry() coverage.

        # Apply accepted changes
        applied = 0
        now_iso = datetime.now().isoformat()
        for item in accepted_items:
            rid = item["recommendation_id"]
            rec = history_by_id[rid]
            wl_name = rec["watchlist_name"]
            symbol = rec["symbol"]
            action = rec["action"]
            current = wl_map.get(wl_name, [])
            try:
                if action == "add" and symbol not in current:
                    current.append(symbol)
                    wl_map[wl_name] = current
                    logger.info("AUDIT apply accept: add %s to %s (rec_id=%d)", symbol, wl_name, rid)
                elif action == "remove" and symbol in current:
                    current.remove(symbol)
                    wl_map[wl_name] = current
                    logger.info("AUDIT apply accept: remove %s from %s (rec_id=%d)", symbol, wl_name, rid)
                else:
                    logger.info("AUDIT apply accept no-op: action=%s symbol=%s wl=%s (rec_id=%d)", action, symbol, wl_name, rid)
                repo.record_decision(rid, "accepted", decided_at=now_iso)
                applied += 1
            except Exception as exc:
                errors.append(f"rec_id={rid}: {exc}")

        # Record rejected
        rejected_count = 0
        for item in rejected_items:
            rid = item["recommendation_id"]
            rec = history_by_id[rid]
            try:
                repo.record_decision(rid, "rejected", decided_at=now_iso)
                logger.info("AUDIT apply reject: %s on %s (rec_id=%d)", rec["action"], rec["symbol"], rid)
                rejected_count += 1
            except Exception as exc:
                errors.append(f"rec_id={rid}: {exc}")

        # Save watchlist.json and hot-reload
        updated_wl = {
            "wheel": wl_map["wheel"],
            "iron_condor": wl_map["iron_condor"],
            "spreads": wl_map["spreads"],
            "updated_at": now_iso,
        }
        wl_path.write_text(json.dumps(updated_wl, indent=2), encoding="utf-8")

        settings.WATCHLIST = wl_map["wheel"]
        settings.IRON_CONDOR_WATCHLIST = wl_map["iron_condor"]
        settings.SPREAD_WATCHLIST = wl_map["spreads"]

        logger.info(
            "Watchlist updated via recommendations: %d wheel, %d iron_condor, %d spreads",
            len(wl_map["wheel"]), len(wl_map["iron_condor"]), len(wl_map["spreads"]),
        )

        return {
            "applied": applied,
            "rejected": rejected_count,
            "updated_watchlists": updated_wl,
            "errors": errors,
        }
    except Exception:
        logger.exception("research/recommendations/apply failed")
        return JSONResponse(status_code=500, content={"error": "apply failed"})
    finally:
        conn.close()


@app.get("/api/research/recommendations/outcomes")
def research_recommendation_outcomes():
    """Return all recommendation outcome rows."""
    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        from database.repositories.outcome_repository import OutcomeRepository
        repo = OutcomeRepository(conn)
        rows = repo.get_all()
        return {"outcomes": rows, "count": len(rows)}
    except Exception:
        logger.exception("research/recommendations/outcomes query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})


# ── Skip breakdown endpoint ──────────────────────────────────────────────────


@app.get("/api/decisions/skip-breakdown")
async def decisions_skip_breakdown(
    account: str | None = None,
    since_days: int = 30,
    group_by: str = "gate",
):
    """Return skip counts broken down by gate and reason within gate."""
    conn = _open_db()
    if conn is None:
        return {
            "window_days": since_days,
            "total_skips": 0,
            "by_gate": [],
            "by_reason_within_gate": {},
            "unclassified_count": 0,
        }
    try:
        repo = DecisionRepository(conn)
        data = repo.get_skip_breakdown(account=account, since_days=since_days)
        return {**data, "window_days": since_days}
    except Exception:
        logger.exception("decisions/skip-breakdown query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


# ── Recommendation scorecard endpoint ─────────────────────────────────────────


@app.get("/api/research/recommendations/scorecard")
async def research_recommendations_scorecard(
    window_days: int = 90,
    since_days: int = 180,
):
    """Return the 4-cell recommendation accuracy scorecard."""
    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        repo = ScorecardRepository(conn)
        cells = repo.get_scorecard(window_days=window_days, since_days=since_days)
        return {
            "window_days": window_days,
            "since_days": since_days,
            "cells": cells,
            "summary": {
                "ground_truth_cells": ["accepted_add", "rejected_remove"],
                "proxy_cells": ["rejected_add", "accepted_remove"],
                "caveat": (
                    "Cells labeled 'proxy_backtest' are not comparable to 'ground_truth_live' cells. "
                    "Proxy cells estimate what would have happened; live cells measure what did happen."
                ),
            },
        }
    except Exception:
        logger.exception("research/recommendations/scorecard query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


# ── Research last-run staleness endpoint ──────────────────────────────────────


@app.get("/api/research/last-run")
async def research_last_run():
    """Return staleness metadata for the research pipeline components."""
    # Try to read last_run.json for scan stats
    last_run_path = DATA_DIR / "research_last_run.json"
    file_data: dict = {}
    if last_run_path.exists():
        try:
            file_data = json.loads(last_run_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    last_run_at = file_data.get("last_run_at")
    last_scan_stats = file_data.get("last_scan_stats")

    most_recent_scores = None
    most_recent_backtest_stats = None
    most_recent_recommendation = None
    most_recent_outcome = None

    conn = _open_db()
    if conn is not None:
        try:
            try:
                row = conn.execute("SELECT MAX(last_updated) FROM symbol_liquidity_scores").fetchone()
                most_recent_scores = row[0] if row and row[0] else None
            except Exception:
                pass
            try:
                row = conn.execute("SELECT MAX(last_updated) FROM symbol_strategy_stats").fetchone()
                most_recent_backtest_stats = row[0] if row and row[0] else None
            except Exception:
                pass
            try:
                row = conn.execute("SELECT MAX(generated_at) FROM watchlist_recommendations").fetchone()
                most_recent_recommendation = row[0] if row and row[0] else None
            except Exception:
                pass
            try:
                row = conn.execute("SELECT MAX(computed_at) FROM recommendation_outcomes").fetchone()
                most_recent_outcome = row[0] if row and row[0] else None
            except Exception:
                pass
        finally:
            conn.close()

    return {
        "last_run_at": last_run_at,
        "last_scan_stats": last_scan_stats,
        "most_recent_scores": most_recent_scores,
        "most_recent_backtest_stats": most_recent_backtest_stats,
        "most_recent_recommendation": most_recent_recommendation,
        "most_recent_outcome": most_recent_outcome,
    }


# ── ORATS usage endpoint ──────────────────────────────────────────────────────


@app.get("/api/orats/usage")
async def orats_usage():
    """Return ORATS API call telemetry from the usage tracker."""
    from data.orats_usage_tracker import ORATSUsageTracker
    tracker = ORATSUsageTracker(path=str(DATA_DIR / "orats_usage.jsonl"))
    recent = tracker.get_recent_runs(since_days=30)
    daily = tracker.get_daily_totals(since_days=30)
    this_week = sum(r["call_count"] for r in tracker.get_recent_runs(since_days=7))
    this_month = sum(r["call_count"] for r in recent)
    return {
        "recent_runs": recent,
        "daily_totals": daily,
        "this_week_total": this_week,
        "this_month_total": this_month,
    }


# ── Claude cost endpoints ────────────────────────────────────────────────────

_WINDOW_DAYS: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "all": None,
}


def _claude_costs_from_db(
    conn: sqlite3.Connection,
    window: str,
    strategy_types: list[str] | None,
) -> dict:
    """Aggregate cost/token stats from the decisions table for the given window."""
    days = _WINDOW_DAYS.get(window)

    # Build time-window predicates
    where_current: list[str] = ["estimated_cost_usd IS NOT NULL"]
    params_current: list = []
    if days is not None:
        where_current.append("timestamp >= datetime('now', ?)")
        params_current.append(f"-{days} days")

    where_prior: list[str] = ["estimated_cost_usd IS NOT NULL"]
    params_prior: list = []
    if days is not None:
        where_prior.append("timestamp >= datetime('now', ?)")
        where_prior.append("timestamp < datetime('now', ?)")
        params_prior.extend([f"-{days * 2} days", f"-{days} days"])
    else:
        # "all" — prior window is empty
        where_prior.append("1=0")

    if strategy_types:
        ph = ",".join(["?"] * len(strategy_types))
        where_current.append(f"strategy_type IN ({ph})")
        params_current.extend(strategy_types)
        where_prior.append(f"strategy_type IN ({ph})")
        params_prior.extend(strategy_types)

    clause_c = "WHERE " + " AND ".join(where_current)
    clause_p = "WHERE " + " AND ".join(where_prior)

    def _agg(clause: str, params: list) -> dict:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*)                              AS decisions_count,
                COALESCE(SUM(estimated_cost_usd), 0) AS total_cost_usd,
                COALESCE(SUM(cache_read_tokens), 0)  AS total_cache_read,
                COALESCE(SUM(cache_creation_tokens), 0) AS total_cache_creation
            FROM decisions
            {clause}
            """,
            params,
        ).fetchone()
        return dict(row) if row else {}

    cur = _agg(clause_c, params_current)
    pri = _agg(clause_p, params_prior)

    total_cost = float(cur.get("total_cost_usd") or 0.0)
    prior_cost = float(pri.get("total_cost_usd") or 0.0)
    decisions_count = int(cur.get("decisions_count") or 0)

    # Filled-trades count (decisions where action is a trade, not skip/hold)
    filled_rows = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM decisions
        {clause_c} AND UPPER(action) NOT IN ('SKIP', 'HOLD')
        """,
        params_current,
    ).fetchone()
    filled_count = int(filled_rows["n"]) if filled_rows else 0
    cost_per_filled = (total_cost / filled_count) if filled_count > 0 else None

    # Cost by outcome bucket (open/close/skip mapped from action vocabulary)
    outcome_rows = conn.execute(
        f"""
        SELECT action,
               COUNT(*)                              AS cnt,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost_usd
        FROM decisions
        {clause_c}
        GROUP BY UPPER(action)
        """,
        params_current,
    ).fetchall()

    _OPEN_ACTIONS = {"SELL_PUT", "SELL_CALL", "OPEN"}
    _CLOSE_ACTIONS = {"CLOSE", "BUY_PUT", "BUY_CALL", "ROLL"}
    _SKIP_ACTIONS = {"SKIP", "HOLD"}

    cost_by_outcome: dict[str, dict] = {
        "open":  {"count": 0, "cost_usd": 0.0},
        "close": {"count": 0, "cost_usd": 0.0},
        "skip":  {"count": 0, "cost_usd": 0.0},
    }
    for r in outcome_rows:
        act = (r["action"] or "").upper()
        cost_val = float(r["cost_usd"] or 0.0)
        cnt = int(r["cnt"] or 0)
        if act in _OPEN_ACTIONS:
            bucket = "open"
        elif act in _CLOSE_ACTIONS:
            bucket = "close"
        else:
            bucket = "skip"
        cost_by_outcome[bucket]["count"] += cnt
        cost_by_outcome[bucket]["cost_usd"] = round(
            cost_by_outcome[bucket]["cost_usd"] + cost_val, 6
        )

    # Cache hit rate
    total_read = int(cur.get("total_cache_read") or 0)
    total_creation = int(cur.get("total_cache_creation") or 0)
    denom_cache = total_read + total_creation
    cache_hit_rate = round(total_read / denom_cache, 4) if denom_cache > 0 else None

    # By model version
    model_rows = conn.execute(
        f"""
        SELECT model_version,
               COUNT(*)                              AS cnt,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost_usd
        FROM decisions
        {clause_c} AND model_version IS NOT NULL
        GROUP BY model_version
        """,
        params_current,
    ).fetchall()
    by_model: dict[str, dict] = {}
    for r in model_rows:
        mv = r["model_version"] or "unknown"
        by_model[mv] = {"count": int(r["cnt"] or 0), "cost_usd": float(r["cost_usd"] or 0.0)}

    return {
        "window": window,
        "total_cost_usd": round(total_cost, 6),
        "prior_window_cost_usd": round(prior_cost, 6),
        "decisions_count": decisions_count,
        "filled_trades_count": filled_count,
        "cost_per_filled_trade_usd": round(cost_per_filled, 6) if cost_per_filled is not None else None,
        "cost_by_outcome": cost_by_outcome,
        "cache_hit_rate": cache_hit_rate,
        "by_model_version": by_model,
    }


@app.get("/api/claude-costs")
def claude_costs(
    window: str = Query(default="7d", pattern="^(7d|30d|90d|all)$"),
    account: str | None = Query(default=None),
):
    """Aggregate Claude API cost and token stats for the requested window."""
    conn = _open_db()
    if conn is None:
        return {
            "window": window,
            "total_cost_usd": 0.0,
            "prior_window_cost_usd": 0.0,
            "decisions_count": 0,
            "filled_trades_count": 0,
            "cost_per_filled_trade_usd": None,
            "cost_by_outcome": {
                "open":  {"count": 0, "cost_usd": 0.0},
                "close": {"count": 0, "cost_usd": 0.0},
                "skip":  {"count": 0, "cost_usd": 0.0},
            },
            "cache_hit_rate": None,
            "by_model_version": {},
        }
    try:
        return _claude_costs_from_db(conn, window, _strategy_filter(account))
    except Exception:
        logger.exception("claude-costs query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


# ── Token usage endpoints ────────────────────────────────────────────────────


@app.get("/api/token-usage")
def token_usage(days: int = Query(default=30, ge=1, le=365)):
    """Daily token usage aggregates for the last N days."""
    conn = _open_db()
    if conn is None:
        return {"daily": []}
    try:
        repo = TokenUsageRepository(conn)
        return {"daily": repo.get_daily_summaries(days)}
    except Exception:
        logger.exception("token-usage query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/token-usage/today")
def token_usage_today():
    """Today's running token usage totals."""
    conn = _open_db()
    if conn is None:
        return {"calls_count": 0, "estimated_cost_usd": 0.0, "cache_hit_rate": 0.0}
    try:
        repo = TokenUsageRepository(conn)
        return repo.get_today_summary()
    except Exception:
        logger.exception("token-usage/today query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/token-usage/summary")
def token_usage_summary():
    """Lifetime stats, per-strategy breakdown, and current prompt size."""
    conn = _open_db()
    prompt_size = _read_json(SNAPSHOTS / "prompt_size.json")
    if conn is None:
        return {
            "lifetime": {"total_calls": 0, "total_cost_usd": 0.0, "overall_cache_hit_rate": 0.0},
            "by_strategy": [],
            "prompt_size": prompt_size,
        }
    try:
        repo = TokenUsageRepository(conn)
        lifetime = repo.get_lifetime_summary()
        by_strategy = repo.get_by_strategy()
        return {
            "lifetime": lifetime,
            "by_strategy": by_strategy,
            "prompt_size": prompt_size,
        }
    except Exception:
        logger.exception("token-usage/summary query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


# ── Cycle summary endpoint ─────────────────────────────────────────────────


def _window_to_cutoff(window: str) -> str | None:
    """Convert a window string (7d/30d/90d/all) to an ISO cutoff timestamp."""
    if window == "all":
        return None
    try:
        days = int(window.rstrip("d"))
    except (ValueError, AttributeError):
        days = 30
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.isoformat(timespec="seconds")


def _empty_cycle_summary(job_run_id: str | None = None) -> dict:
    return {
        "job_run_id": job_run_id,
        "cycle_started_at": None,
        "cycle_type": "unknown",
        "symbols_evaluated": 0,
        "pre_check_skipped": {"total": 0, "by_reason": {}},
        "sent_to_claude": 0,
        "claude_outcomes": {"SKIP": 0, "OPEN": 0, "CLOSE": 0, "HOLD": 0},
        "guardrail_rejections": {"total": 0, "by_rule": {}},
        "orders_placed": 0,
        "orders_details": [],
    }


@app.get("/api/cycle-summary")
def cycle_summary(job_run_id: str | None = Query(default=None)):
    """Return an aggregated summary of the latest (or specified) job run.

    Groups all decisions sharing a job_run_id and computes the funnel:
    symbols evaluated → pre-check skipped → sent to Claude → Claude outcomes
    → guardrail rejections → orders placed.

    Query params:
        job_run_id: specific run to fetch; omit for most recent.
    """
    conn = _open_db()
    if conn is None:
        return _empty_cycle_summary()
    try:
        if job_run_id is None:
            row = conn.execute(
                "SELECT job_run_id, MIN(timestamp) AS started_at "
                "FROM decisions WHERE job_run_id IS NOT NULL "
                "GROUP BY job_run_id ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if not row or not row["job_run_id"]:
                return _empty_cycle_summary()
            job_run_id = row["job_run_id"]

        rows = conn.execute(
            """
            SELECT d.underlying, d.strategy_type, d.action,
                   d.skip_gate, d.skip_reason_code, d.pre_check_verdict,
                   d.timestamp, d.id,
                   t.symbol  AS trade_symbol,
                   t.limit_price AS trade_limit_price,
                   t.strategy_type AS trade_strategy_type
            FROM decisions d
            LEFT JOIN trades t ON t.decision_id = d.id
            WHERE d.job_run_id = ?
            ORDER BY d.timestamp
            """,
            (job_run_id,),
        ).fetchall()

        if not rows:
            return _empty_cycle_summary(job_run_id)

        started_at = rows[0]["timestamp"]
        # Extract job type from the prefix: "market_open.<uuid>" → "market_open"
        cycle_type = job_run_id.split(".")[0] if "." in job_run_id else "unknown"

        symbols_evaluated = len(rows)

        # Pre-check skips (Claude never called)
        skip_rows = [r for r in rows if (r["pre_check_verdict"] or "") == "SKIP"]
        by_reason: dict[str, int] = {}
        for r in skip_rows:
            code = r["skip_reason_code"] or "unknown"
            by_reason[code] = by_reason.get(code, 0) + 1

        # Rows where Claude was actually consulted for entry decisions
        open_rows = [r for r in rows if (r["pre_check_verdict"] or "") == "OPEN"]

        # Normalise Claude's action to the 4-bucket vocabulary
        claude_outcomes: dict[str, int] = {"SKIP": 0, "OPEN": 0, "CLOSE": 0, "HOLD": 0}
        for r in open_rows:
            act = (r["action"] or "").upper()
            if act in ("SELL_PUT", "SELL_CALL", "OPEN"):
                claude_outcomes["OPEN"] += 1
            elif act == "SKIP":
                claude_outcomes["SKIP"] += 1
            elif act == "CLOSE":
                claude_outcomes["CLOSE"] += 1
            else:  # HOLD, ROLL, or anything else
                claude_outcomes["HOLD"] += 1

        # Guardrail rejections
        guardrail_rows = [r for r in rows if (r["skip_gate"] or "").lower() == "guardrail"]
        by_rule: dict[str, int] = {}
        for r in guardrail_rows:
            rule = r["skip_reason_code"] or "guardrail_other"
            by_rule[rule] = by_rule.get(rule, 0) + 1

        # Orders placed: decisions that have a trade row attached
        order_rows = [r for r in rows if r["trade_symbol"] is not None]
        orders_details = [
            {
                "symbol": r["underlying"],
                "strategy": r["strategy_type"],
                "action": r["action"],
                "contract": r["trade_symbol"],
                "limit_price": r["trade_limit_price"],
            }
            for r in order_rows
        ]

        return {
            "job_run_id": job_run_id,
            "cycle_started_at": started_at,
            "cycle_type": cycle_type,
            "symbols_evaluated": symbols_evaluated,
            "pre_check_skipped": {"total": len(skip_rows), "by_reason": by_reason},
            "sent_to_claude": len(open_rows),
            "claude_outcomes": claude_outcomes,
            "guardrail_rejections": {"total": len(guardrail_rows), "by_rule": by_rule},
            "orders_placed": len(order_rows),
            "orders_details": orders_details,
        }
    except Exception:
        logger.exception("cycle-summary query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


# ── Claude agreement metric endpoint ──────────────────────────────────────


@app.get("/api/claude-agreement")
def claude_agreement(window: str = Query(default="30d")):
    """Return the Claude-vs-pre-check agreement metric for a time window.

    Measures how often Claude disagrees with the pre-check layer:
      - claude_skip_rate_when_precheck_says_open: fraction of pre-check-OPEN
        decisions where Claude said SKIP.
      - claude_open_rate_when_precheck_says_skip: fraction of pre-check-SKIP
        decisions where Claude opened anyway (should be ~0 in normal operation).

    Query params:
        window: "7d" | "30d" | "90d" | "all" (default "30d")
    """
    conn = _open_db()
    if conn is None:
        return {
            "window": window,
            "decisions_evaluated": 0,
            "precheck_open_count": 0,
            "claude_skip_rate_when_precheck_says_open": None,
            "precheck_skip_count": 0,
            "claude_open_rate_when_precheck_says_skip": None,
            "daily_series": [],
        }
    try:
        cutoff = _window_to_cutoff(window)
        query = """
            SELECT pre_check_verdict, UPPER(action) AS action,
                   DATE(timestamp) AS dt
            FROM decisions
            WHERE pre_check_verdict IN ('OPEN', 'SKIP')
        """
        params: list = []
        if cutoff is not None:
            query += " AND timestamp >= ?"
            params.append(cutoff)

        rows = conn.execute(query, params).fetchall()

        precheck_open = [r for r in rows if r["pre_check_verdict"] == "OPEN"]
        precheck_skip = [r for r in rows if r["pre_check_verdict"] == "SKIP"]

        precheck_open_count = len(precheck_open)
        precheck_skip_count = len(precheck_skip)

        if precheck_open_count > 0:
            n_claude_skip = sum(1 for r in precheck_open if r["action"] == "SKIP")
            skip_rate: float | None = round(n_claude_skip / precheck_open_count, 4)
        else:
            skip_rate = None

        # With current architecture this is always 0 since Claude is not
        # consulted when pre_check_verdict = 'SKIP'. Computed defensively.
        if precheck_skip_count > 0:
            n_claude_open = sum(
                1 for r in precheck_skip
                if r["action"] not in ("SKIP", "HOLD", None, "")
            )
            open_rate: float | None = round(n_claude_open / precheck_skip_count, 4)
        else:
            open_rate = None

        # Daily series: one entry per calendar day in the window (pre-check
        # OPEN decisions only, since that's what drives the skip rate).
        daily: dict[str, dict[str, int]] = {}
        for r in precheck_open:
            dt = r["dt"] or "unknown"
            if dt not in daily:
                daily[dt] = {"open_count": 0, "skip_count": 0}
            daily[dt]["open_count"] += 1
            if r["action"] == "SKIP":
                daily[dt]["skip_count"] += 1

        daily_series = []
        for dt in sorted(daily):
            d = daily[dt]
            oc = d["open_count"]
            sc = d["skip_count"]
            daily_series.append({
                "date": dt,
                "skip_when_open_rate": round(sc / oc, 4) if oc > 0 else None,
                "sample_size": oc,
            })

        return {
            "window": window,
            "decisions_evaluated": len(rows),
            "precheck_open_count": precheck_open_count,
            "claude_skip_rate_when_precheck_says_open": skip_rate,
            "precheck_skip_count": precheck_skip_count,
            "claude_open_rate_when_precheck_says_skip": open_rate,
            "daily_series": daily_series,
        }
    except Exception:
        logger.exception("claude-agreement query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


# ── Evaluations endpoints ────────────────────────────────────────────────────

_VALID_SPOT_CHECK_VERDICTS = frozenset({"agree", "disagree", "unclear"})


def _parse_json_field(raw: str | None) -> list | dict | None:
    """Parse a JSON text field, returning None on failure."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _eval_review_status(row: dict) -> str:
    """Derive review_status from reviewed_at + action_note."""
    if row.get("reviewed_at"):
        return "reviewed-with-action" if row.get("action_note") else "reviewed"
    return "pending"


def _format_eval_summary(row: dict, disagree_rate: float | None) -> dict:
    """Serialise a monthly_evaluations row for the list endpoint."""
    flags = _parse_json_field(row.get("flags_json")) or []
    score_dist = _parse_json_field(row.get("score_distribution_json")) or {}
    overall = score_dist.get("overall", {}) if isinstance(score_dist, dict) else {}
    return {
        "month": row["month"],
        "generated_at": row["created_at"],
        "decisions_scored": row.get("decisions_evaluated", 0),
        "closed_trades_in_window": overall.get("closed_trades_in_window"),
        "insufficient_sample": None,  # not persisted; see monthly_evaluation.py
        "flag_count": len(flags) if isinstance(flags, list) else 0,
        "review_status": _eval_review_status(row),
        "judge_operator_disagreement": disagree_rate,
    }


@app.get("/api/evaluations")
def evaluations_list(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List monthly evaluation summaries, newest first."""
    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        from database.repositories.judge_spot_checks_repository import JudgeSpotChecksRepository
        from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository

        evals_repo = MonthlyEvaluationsRepository(conn)
        spot_repo = JudgeSpotChecksRepository(conn)

        rows = evals_repo.get_list(limit=limit, offset=offset)
        total = conn.execute("SELECT COUNT(*) FROM monthly_evaluations").fetchone()[0]

        evaluations = [
            _format_eval_summary(row, spot_repo.get_disagreement_rate(row["month"]))
            for row in rows
        ]
        return {"evaluations": evaluations, "total": total}
    except Exception:
        logger.exception("evaluations list query failed")
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/evaluations/{month}/flagged-decisions")
def evaluations_flagged_decisions(month: str):
    """Per-flag: the 5 lowest-scoring decisions with full context and score rows."""
    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        from database.repositories.decision_scores_repository import DecisionScoresRepository
        from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository

        evals_repo = MonthlyEvaluationsRepository(conn)
        scores_repo = DecisionScoresRepository(conn)

        row = evals_repo.get_by_month(month)
        if row is None:
            return JSONResponse(status_code=404, content={"error": f"evaluation for {month!r} not found"})

        flags = _parse_json_field(row.get("flags_json")) or []
        if not isinstance(flags, list):
            flags = []

        result_flags = []
        for flag in flags:
            decision_ids = flag.get("lowest_scoring_decisions") or []
            decisions = []
            if decision_ids:
                placeholders = ",".join(["?"] * len(decision_ids))
                decision_rows = conn.execute(
                    f"SELECT * FROM decisions WHERE id IN ({placeholders})",
                    decision_ids,
                ).fetchall()
                decision_map = {r["id"]: dict(r) for r in decision_rows}

                for did in decision_ids:
                    d = decision_map.get(did)
                    if d is None:
                        continue
                    if d.get("context_json"):
                        try:
                            d["context"] = json.loads(d["context_json"])
                        except (json.JSONDecodeError, TypeError):
                            d["context"] = None
                    if isinstance(d.get("reasoning"), str):
                        try:
                            d["reasoning"] = json.loads(d["reasoning"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    d["scores"] = scores_repo.get_by_decision(did)
                    decisions.append(d)

            result_flags.append({
                "strategy": flag.get("strategy"),
                "dimension": flag.get("dimension"),
                "severity": flag.get("severity"),
                "reason": flag.get("reason"),
                "decisions": decisions,
            })

        return {"month": month, "flags": result_flags}
    except Exception:
        logger.exception("evaluations flagged-decisions query failed for %s", month)
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/evaluations/{month}/spot-check-queue")
def evaluations_spot_check_queue(
    month: str,
    limit: int = Query(default=20, ge=1, le=100),
):
    """Return pending spot-check items for the month with full decision context."""
    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        from database.repositories.decision_scores_repository import DecisionScoresRepository
        from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository

        evals_repo = MonthlyEvaluationsRepository(conn)
        scores_repo = DecisionScoresRepository(conn)

        if evals_repo.get_by_month(month) is None:
            return JSONResponse(status_code=404, content={"error": f"evaluation for {month!r} not found"})

        score_rows = scores_repo.get_spot_check_queue(month, limit=limit)

        queue = []
        for score in score_rows:
            decision_row = conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (score["decision_id"],)
            ).fetchone()
            decision = dict(decision_row) if decision_row else None
            if decision:
                if decision.get("context_json"):
                    try:
                        decision["context"] = json.loads(decision["context_json"])
                    except (json.JSONDecodeError, TypeError):
                        decision["context"] = None
                if isinstance(decision.get("reasoning"), str):
                    try:
                        decision["reasoning"] = json.loads(decision["reasoning"])
                    except (json.JSONDecodeError, TypeError):
                        pass

            score_out = dict(score)
            if score_out.get("dimension_scores_json"):
                try:
                    score_out["dimension_scores"] = json.loads(score_out["dimension_scores_json"])
                except (json.JSONDecodeError, TypeError):
                    score_out["dimension_scores"] = None

            queue.append({"decision": decision, "score": score_out})

        return {"month": month, "queue": queue}
    except Exception:
        logger.exception("evaluations spot-check-queue query failed for %s", month)
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.get("/api/evaluations/{month}")
def evaluations_detail(month: str):
    """Return the full monthly evaluation row with parsed flags and score distribution."""
    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        from database.repositories.judge_spot_checks_repository import JudgeSpotChecksRepository
        from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository

        evals_repo = MonthlyEvaluationsRepository(conn)
        spot_repo = JudgeSpotChecksRepository(conn)

        row = evals_repo.get_by_month(month)
        if row is None:
            return JSONResponse(status_code=404, content={"error": f"evaluation for {month!r} not found"})

        return {
            "month": row["month"],
            "generated_at": row["created_at"],
            "decisions_scored": row.get("decisions_evaluated", 0),
            "avg_score": row.get("avg_score"),
            "pct_pass": row.get("pct_pass"),
            "reviewed_at": row.get("reviewed_at"),
            "action_note": row.get("action_note"),
            "review_status": _eval_review_status(row),
            "flag_summary": _parse_json_field(row.get("flags_json")) or [],
            "score_distribution": _parse_json_field(row.get("score_distribution_json")) or {},
            "judge_operator_disagreement": spot_repo.get_disagreement_rate(month),
        }
    except Exception:
        logger.exception("evaluations detail query failed for %s", month)
        return JSONResponse(status_code=500, content={"error": "query failed"})
    finally:
        conn.close()


@app.post("/api/evaluations/{month}/spot-checks")
async def evaluations_submit_spot_check(month: str, request: Request):
    """Submit an operator spot-check verdict for a decision score."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    decision_score_id = body.get("decision_score_id")
    operator_verdict = body.get("operator_verdict")
    note = body.get("note")

    if not isinstance(decision_score_id, int):
        return JSONResponse(status_code=400, content={"error": "'decision_score_id' must be an integer"})
    if operator_verdict not in _VALID_SPOT_CHECK_VERDICTS:
        return JSONResponse(
            status_code=400,
            content={"error": f"'operator_verdict' must be one of: {sorted(_VALID_SPOT_CHECK_VERDICTS)}"},
        )
    if note is not None and not isinstance(note, str):
        return JSONResponse(status_code=400, content={"error": "'note' must be a string or null"})

    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        from database.repositories.decision_scores_repository import DecisionScoresRepository
        from database.repositories.judge_spot_checks_repository import JudgeSpotChecksRepository
        from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository

        evals_repo = MonthlyEvaluationsRepository(conn)
        scores_repo = DecisionScoresRepository(conn)
        spot_repo = JudgeSpotChecksRepository(conn)

        if evals_repo.get_by_month(month) is None:
            return JSONResponse(status_code=404, content={"error": f"evaluation for {month!r} not found"})

        score_row = conn.execute(
            "SELECT * FROM decision_scores WHERE id = ?", (decision_score_id,)
        ).fetchone()
        if score_row is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"decision_score {decision_score_id} not found"},
            )
        score_dict = dict(score_row)
        if not score_dict.get("scored_at", "").startswith(month):
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"decision_score {decision_score_id} does not belong to month {month!r}"
                    )
                },
            )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        check_id = spot_repo.insert({
            "decision_score_id": decision_score_id,
            "submitted_at": now,
            "judge_score": score_dict.get("total_score"),
            "operator_verdict": operator_verdict,
            "verdict_notes": note,
            "checked_at": now,
        })
        scores_repo.mark_spot_check_submitted(decision_score_id)

        created = conn.execute(
            "SELECT * FROM judge_spot_checks WHERE id = ?", (check_id,)
        ).fetchone()
        return {"spot_check": dict(created) if created else {"id": check_id}}
    except Exception:
        logger.exception("evaluations spot-checks POST failed for %s", month)
        return JSONResponse(status_code=500, content={"error": "submit failed"})
    finally:
        conn.close()


@app.post("/api/evaluations/{month}/mark-reviewed")
async def evaluations_mark_reviewed(month: str, request: Request):
    """Mark a monthly evaluation as reviewed, optionally with an action note."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    action_note = body.get("action_note")
    if action_note is not None and not isinstance(action_note, str):
        return JSONResponse(status_code=400, content={"error": "'action_note' must be a string or null"})

    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository

        evals_repo = MonthlyEvaluationsRepository(conn)

        if evals_repo.get_by_month(month) is None:
            return JSONResponse(status_code=404, content={"error": f"evaluation for {month!r} not found"})

        note = action_note.strip() if action_note else None
        evals_repo.mark_reviewed(month, note or None)

        updated = evals_repo.get_by_month(month)
        return {
            "month": updated["month"],
            "review_status": _eval_review_status(updated),
            "reviewed_at": updated["reviewed_at"],
            "action_note": updated["action_note"],
        }
    except Exception:
        logger.exception("evaluations mark-reviewed POST failed for %s", month)
        return JSONResponse(status_code=500, content={"error": "mark-reviewed failed"})
    finally:
        conn.close()


# ── Strategy health funnel ───────────────────────────────────────────────────


@app.get("/api/strategy-health")
def strategy_health(
    weeks: int = Query(12, ge=1, le=52),
    strategy: str | None = Query(None),
):
    """Weekly funnel per strategy for the last N weeks.

    Paper-trading context: the returned counts reflect Claude decision quality
    and guardrail behaviour. They do NOT reflect strategy profitability —
    paper fills are unrealistic (mid-price, no slippage, no fees).

    Returns:
        {
          "weeks": [...],          # list of funnel rows (see StrategyHealthRepository)
          "generated_at": "<iso>",
          "paper_mode": true
        }
    """
    if not settings.STRATEGY_HEALTH_PAGE_ENABLED:
        return JSONResponse(status_code=404, content={"error": "not found"})

    conn = _open_db()
    if conn is None:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    try:
        from database.repositories.strategy_health import StrategyHealthRepository
        repo = StrategyHealthRepository(conn)
        strategy_filter = [strategy] if strategy else None
        rows = repo.get_strategy_health_funnel(
            weeks_back=weeks,
            strategy_types=strategy_filter,
        )
        return {
            "weeks": rows,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "paper_mode": True,
        }
    except Exception:
        logger.exception("strategy-health endpoint failed")
        return JSONResponse(status_code=500, content={"error": "internal error"})
    finally:
        conn.close()


# ── Static frontend (SPA catch-all) ────────────────────────
# Defined as a route (not a mount) so that unknown paths like
# /reasoning fall back to index.html instead of 404-ing.
# All /api/* routes defined above take precedence over this
# catch-all because FastAPI checks explicit routes first.

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_READY = _STATIC_DIR.exists() and any(
    p.name != ".gitkeep" for p in _STATIC_DIR.iterdir()
)

if not _STATIC_READY:
    logger.info(
        "Frontend static dir empty (%s) — skipping SPA route. "
        "Run `cd frontend && npm run build` to populate.",
        _STATIC_DIR,
    )
else:
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        candidate = _STATIC_DIR / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
