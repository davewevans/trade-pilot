"""FastAPI server that reads snapshot files and serves them to the dashboard.

Runs independently from the scheduler.  Start with::

    python api/run.py          # default: localhost:8000
    uvicorn api.server:app     # alternative
"""

import json
import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Log snapshot directory status on startup."""
    logger.info("Snapshot directory: %s", SNAPSHOTS)
    for name in ("portfolio.json", "context.json", "circuit_breakers.json", "decisions.jsonl"):
        p = SNAPSHOTS / name
        if not p.exists():
            logger.warning("Snapshot file not found (will be created by scheduler): %s", p)
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
        "timestamp": datetime.now().isoformat(timespec="seconds"),
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


@app.get("/api/decisions")
def decisions(
    limit: int = Query(default=50, ge=1, le=500),
    underlying: str | None = Query(default=None),
    action: str | None = Query(default=None),
):
    all_decisions = _read_jsonl(SNAPSHOTS / "decisions.jsonl")

    # Filter
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
    # Most recent first, then apply limit
    recent = list(reversed(all_decisions))[:limit]
    return {"decisions": recent, "total": total}


@app.get("/api/decisions/stats")
def decisions_stats():
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

    # Win rate from journal (closed positions with pnl)
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
def performance():
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

        # Build equity curve from fill_price entries
        ts = e.get("timestamp")
        if ts and e.get("fill_price") is not None:
            equity_curve.append({"timestamp": ts, "equity": round(total_pnl, 2)})

    # Read portfolio snapshot for current equity baseline
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
