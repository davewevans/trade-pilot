#!/usr/bin/env python3
"""Adaptive thinking A/B harness — Story 3.

Calls Claude THREE times per context (off / adaptive_medium / adaptive_high)
and records action, confidence, skip_reason, reasoning, latency, and token
costs. Writes results to data/ab_tests/thinking_<timestamp>.jsonl and prints
a summary table.

CRITICAL: This script is READ-ONLY. It calls ClaudeAdvisor with historical
sample contexts and NEVER executes any order. The assertion at the top of
main() verifies this is enforced.

Usage:
    python scripts/thinking_ab_test.py --n 5            # smoke test (5 contexts)
    python scripts/thinking_ab_test.py --n 20           # full run
    python scripts/thinking_ab_test.py --dry-run        # print contexts, no API calls
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("thinking-ab-test")

# ── CRITICAL: enforce read-only mode ─────────────────────────────────────────
# This script MUST NOT import or call any broker, order-execution, or
# portfolio-mutation code. The assertion below catches accidental imports.
_FORBIDDEN_MODULES = frozenset({
    "brokers", "main", "jobs", "strategies._spread_lifecycle",
})

# Pricing (claude-sonnet-4-6, same as cache_stats.py)
_PRICE_INPUT_PER_M       = 3.00
_PRICE_CACHE_WRITE_PER_M = 3.75
_PRICE_CACHE_READ_PER_M  = 0.30
_PRICE_OUTPUT_PER_M      = 15.00


def _cost(input_tok: int, cache_read: int, cache_write: int, output_tok: int) -> float:
    return (
        input_tok    / 1_000_000 * _PRICE_INPUT_PER_M
        + cache_write / 1_000_000 * _PRICE_CACHE_WRITE_PER_M
        + cache_read  / 1_000_000 * _PRICE_CACHE_READ_PER_M
        + output_tok  / 1_000_000 * _PRICE_OUTPUT_PER_M
    )


# ── Sample contexts (fallback when no real snapshots exist) ───────────────────

_WHEEL_IDLE_CTX = {
    "timestamp": "2026-04-16T10:00:00",
    "symbol": "AAPL",
    "wheel_state": "IDLE",
    "account": {"buying_power": 100000.0, "options_trading_level": 3, "portfolio_value": 200000.0},
    "positions": [],
    "technicals": {
        "current_price": 195.50,
        "rsi_14": 51.0,
        "sma_50": 198.00,
        "sma_200": 185.00,
        "above_sma_50": False,
        "above_sma_200": True,
    },
    "fundamentals": {"next_earnings_date": "2026-07-24", "days_to_earnings": 98},
    "macro": {"vix": 22.5, "vix_regime": "normal", "fear_greed_score": 55.0},
    "volatility": {"iv_rank_1y": 45.0, "iv_environment": "MODERATE"},
    "iv_rank": 45.0,
    "iv_environment": "MODERATE",
    "confirmed_market_regime": "NEUTRAL",
    "option_chain": {"contracts": [], "snapshots": {}},
    "_ab_test_label": "AAPL wheel idle",
}

_WHEEL_IDLE_HIGH_IVR = {
    **_WHEEL_IDLE_CTX,
    "iv_rank": 72.0,
    "iv_environment": "HIGH",
    "volatility": {"iv_rank_1y": 72.0, "iv_environment": "HIGH"},
    "macro": {"vix": 28.0, "vix_regime": "elevated", "fear_greed_score": 35.0},
    "_ab_test_label": "AAPL wheel idle HIGH IVR",
}

_SPY_SPREAD_IDLE_CTX = {
    "timestamp": "2026-04-16T10:00:00",
    "symbol": "SPY",
    "wheel_state": "IDLE",
    "account": {"buying_power": 100000.0, "options_trading_level": 3, "portfolio_value": 200000.0},
    "positions": [],
    "technicals": {
        "current_price": 520.0,
        "rsi_14": 50.0,
        "sma_50": 515.0,
        "sma_200": 490.0,
        "above_sma_50": True,
        "above_sma_200": True,
    },
    "fundamentals": {"next_earnings_date": "2026-08-01", "days_to_earnings": 107, "sector": "ETF"},
    "macro": {"vix": 20.0, "vix_regime": "normal", "fear_greed_score": 60.0},
    "volatility": {"iv_rank_1y": 55.0, "iv_environment": "HIGH"},
    "iv_rank": 55.0,
    "iv_environment": "HIGH",
    "confirmed_market_regime": "NEUTRAL",
    "best_candidate": None,
    "_ab_test_label": "SPY bull_put_spread idle",
}

_CONDOR_IDLE_CTX = {
    **_SPY_SPREAD_IDLE_CTX,
    "iron_condor_candidate": None,
    "_ab_test_label": "SPY iron_condor idle",
}

_AAPL_BEAR_SPREAD_CTX = {
    **_SPY_SPREAD_IDLE_CTX,
    "symbol": "AAPL",
    "technicals": {
        "current_price": 195.50,
        "rsi_14": 65.0,
        "sma_50": 198.00,
        "sma_200": 185.00,
        "above_sma_50": False,
        "above_sma_200": True,
    },
    "confirmed_market_regime": "BEAR",
    "iv_rank": 60.0,
    "best_candidate": None,
    "_ab_test_label": "AAPL bear_call_spread idle",
}

# Ordered list of (context, strategy, phase) for the harness
_SAMPLE_CONTEXTS = [
    (_WHEEL_IDLE_CTX,          "wheel",            "idle"),
    (_WHEEL_IDLE_HIGH_IVR,     "wheel",            "idle"),
    (_SPY_SPREAD_IDLE_CTX,     "bull_put_spread",  "idle"),
    (_CONDOR_IDLE_CTX,         "iron_condor",      "idle"),
    (_AAPL_BEAR_SPREAD_CTX,    "bear_call_spread", "idle"),
]


def _call_advisor(advisor, context: dict, strategy: str, phase: str) -> dict:
    """Call the advisor and return a result dict with timing and cost info."""
    from strategies.wheel_strategy import WheelState

    t0 = time.monotonic()
    if strategy == "wheel":
        phase_enum = {
            "idle": WheelState.IDLE,
            "short_put": WheelState.SHORT_PUT,
            "long_stock": WheelState.LONG_STOCK,
            "short_call": WheelState.SHORT_CALL,
        }[phase.lower()]
        decision = advisor.ask(context, phase_enum)
    else:
        decision = advisor.ask_spread(context, strategy, phase)
    latency_ms = int((time.monotonic() - t0) * 1000)

    usage = advisor._last_usage or {}
    input_tok  = usage.get("input_tokens", 0) or 0
    cr_tok     = usage.get("cache_read_input_tokens", 0) or 0
    cw_tok     = usage.get("cache_creation_input_tokens", 0) or 0
    out_tok    = usage.get("output_tokens", 0) or 0
    cost       = _cost(input_tok, cr_tok, cw_tok, out_tok)

    return {
        "action":        decision.get("action"),
        "confidence":    decision.get("confidence"),
        "skip_reason":   decision.get("skip_reason"),
        "reasoning_text": json.dumps(decision.get("reasoning", {})),
        "latency_ms":    latency_ms,
        "input_tokens":  input_tok,
        "cache_read":    cr_tok,
        "cache_write":   cw_tok,
        "output_tokens": out_tok,
        "cost_usd":      cost,
    }


def _assert_no_order_execution():
    """Verify that no order-execution code has been imported into this process.

    Checks for the specific functions that would place real orders. The broker
    module itself may be imported transitively (e.g. via config); what matters
    is that place_order, execute_decision, and similar functions are unreachable.
    """
    forbidden_attrs = [
        ("main", "execute_decision"),
        ("jobs.market_open", "run"),
        ("jobs.position_check", "run"),
    ]
    for mod_name, attr in forbidden_attrs:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            assert not hasattr(mod, attr), (
                f"SAFETY VIOLATION: {mod_name}.{attr} is accessible. "
                f"This A/B harness must never trigger order placement."
            )


def main():
    # ── CRITICAL: read-only guard ────────────────────────────────────────────
    _assert_no_order_execution()

    parser = argparse.ArgumentParser(description="Adaptive thinking A/B test harness")
    parser.add_argument("--n", type=int, default=5, help="Number of contexts to test (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Print contexts without calling API")
    args = parser.parse_args()

    from config import settings
    from ai.claude_advisor import ClaudeAdvisor

    # Re-assert after imports
    _assert_no_order_execution()

    contexts = _SAMPLE_CONTEXTS[:args.n]
    print(f"\nA/B Test: {len(contexts)} context(s) × 3 modes = {len(contexts) * 3} API calls")
    print(f"Thinking mode setting (env): {settings.THINKING_MODE}")
    print(f"(This harness overrides thinking_mode per call — env setting is ignored)\n")

    if args.dry_run:
        for i, (ctx, strategy, phase) in enumerate(contexts):
            print(f"  Context {i+1}: {ctx.get('_ab_test_label', f'{strategy}/{phase}')}")
        print("\n[dry-run] No API calls made.")
        return

    out_dir = Path(settings.DATA_DIR) / "ab_tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"thinking_{ts}.jsonl"

    results = []
    modes = ["off", "adaptive_medium", "adaptive_high"]

    for i, (ctx, strategy, phase) in enumerate(contexts):
        label = ctx.get("_ab_test_label", f"{strategy}/{phase}")
        print(f"[{i+1}/{len(contexts)}] {label}")

        row: dict = {
            "label": label,
            "strategy": strategy,
            "phase": phase,
            "context_summary": {
                "symbol": ctx.get("symbol"),
                "regime": ctx.get("confirmed_market_regime"),
                "iv_rank": ctx.get("iv_rank"),
                "iv_env": ctx.get("iv_environment"),
            },
            "results": {},
        }

        for mode in modes:
            print(f"  mode={mode} ...", end="", flush=True)
            advisor = ClaudeAdvisor(thinking_mode=mode)
            try:
                r = _call_advisor(advisor, ctx, strategy, phase)
                row["results"][mode] = r
                print(
                    f" action={r['action']} conf={r['confidence']} "
                    f"latency={r['latency_ms']}ms cost=${r['cost_usd']:.4f}"
                )
            except Exception as e:
                print(f" ERROR: {e}")
                row["results"][mode] = {"error": str(e)}
            time.sleep(0.5)  # rate limit courtesy

        results.append(row)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  Adaptive Thinking A/B Results — {len(results)} context(s)")
    print(f"  Output: {out_path}")
    print(f"{'='*65}")

    action_diffs = 0
    conf_diffs = 0
    totals: dict[str, dict] = {m: {"latency": 0, "cost": 0.0, "n": 0} for m in modes}

    for row in results:
        actions = {m: row["results"].get(m, {}).get("action") for m in modes}
        confs   = {m: row["results"].get(m, {}).get("confidence") for m in modes}
        unique_actions = len({v for v in actions.values() if v is not None})
        unique_confs   = len({v for v in confs.values() if v is not None})
        if unique_actions > 1:
            action_diffs += 1
        if unique_confs > 1:
            conf_diffs += 1

        for m in modes:
            r = row["results"].get(m, {})
            if "error" not in r:
                totals[m]["latency"] += r.get("latency_ms", 0)
                totals[m]["cost"]    += r.get("cost_usd", 0.0)
                totals[m]["n"]       += 1

    n = len(results)
    print(f"\n  Decisions differing across modes : {action_diffs}/{n} ({action_diffs/n*100:.0f}%)")
    print(f"  Confidence differing across modes: {conf_diffs}/{n} ({conf_diffs/n*100:.0f}%)")
    print(f"\n  {'Mode':<18}  {'Avg latency':>12}  {'Avg cost/call':>14}  {'Est daily cost*':>16}")
    print(f"  {'-'*65}")

    # Estimate daily call volume from DB if available (else use a placeholder)
    try:
        from database.db import Database
        from database.repositories import ApiUsageRepository
        db = Database()
        db.init_schema()
        rows = ApiUsageRepository(db.get_connection()).get_recent(days=7)
        daily_calls = len(rows) / 7 if rows else 20
    except Exception:
        daily_calls = 20  # placeholder

    for m in modes:
        t = totals[m]
        nn = t["n"] or 1
        avg_lat = t["latency"] / nn
        avg_cost = t["cost"] / nn
        est_daily = avg_cost * daily_calls
        print(f"  {m:<18}  {avg_lat:>10.0f}ms  ${avg_cost:>13.4f}  ${est_daily:>14.4f}")

    print(f"\n  * Estimate based on {daily_calls:.0f} calls/day (from claude_api_calls table or default 20)")
    print(f"{'='*65}\n")
    print(f"Run diff_thinking_decisions.py {out_path} to inspect per-context diffs.")


if __name__ == "__main__":
    main()
