"""FastAPI server that reads snapshot files and serves them to the dashboard.

Runs independently from the scheduler.  Start with::

    python api/run.py          # default: localhost:8000
    uvicorn api.server:app     # alternative
"""

import json
import logging
import sqlite3
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from database.repositories import DecisionRepository, TradeRepository

logger = logging.getLogger(__name__)


# Per-leg cash flow sign convention for the trades table. Used when
# the schema's `premium_credit` is unpopulated (current state). Caller
# is responsible for filtering out NULL `fill_price` rows BEFORE this.
_SELL_TRADE_TYPES = {"SELL_PUT", "SELL_CALL"}
_BUY_TRADE_TYPES = {"BUY_PUT", "BUY_CALL"}


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


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Log snapshot directory status on startup."""
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

    yield


app = FastAPI(title="trade-pilot", version="0.1.0", lifespan=lifespan)

# ── CORS (allow all origins during development) ────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── paths ───────────────────────────────────────────────────
SNAPSHOTS = settings.SNAPSHOTS_DIR
DATA_DIR = settings.DATA_DIR
JOURNAL_PATH = settings.JOURNAL_PATH
LOCK_PATH = DATA_DIR / "HALTED.lock"
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


# ── endpoints ───────────────────────────────────────────────


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        # Timezone-aware ISO string so the browser can convert to local time.
        # `datetime.now()` (naive) gets interpreted as local time by JS, which
        # produces the wrong wall-clock when the server runs in UTC (Render).
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "halted": LOCK_PATH.exists(),
    }


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
    return data


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


@app.get("/api/accounts")
def all_accounts():
    """Return a summary of all three accounts for the dashboard cards.

    Returns a dict keyed by account name. Missing accounts return
    null for that key — the frontend handles the empty state.
    """
    result = {}
    for name in ("wheel", "iron_condor", "spreads"):
        data = _read_json(SNAPSHOTS / f"portfolio_{name}.json")
        result[name] = data  # None if missing — frontend shows "—"
    return result


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


# ── Static frontend ────────────────────────────────────────
# Mounted at the end so all /api/* routes take precedence.
# Skipped if `api/static/` is empty (pre-build dev environment).

from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists() and any(
    p.name != ".gitkeep" for p in _STATIC_DIR.iterdir()
):
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="frontend")
else:
    logger.info(
        "Frontend static dir empty (%s) — skipping mount. "
        "Run `cd frontend && npm run build` to populate.",
        _STATIC_DIR,
    )
