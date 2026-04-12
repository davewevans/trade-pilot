"""Test the Claude spread-strategy decision flow end-to-end (dry-run).

Calls ``ClaudeAdvisor.ask_spread()`` with a built (or sampled) context
plus injected sample candidates / open-spread state. Prints the full
decision JSON, a readable summary, and a PASS/FAIL line. Also runs a
quick check that ``Guardrails.validate_bull_put_spread_entry`` rejects
a decision missing ``short_put_symbol``.

Usage:
    python scripts/test_spread_decisions.py --strategy bull_put_spread --phase idle
    python scripts/test_spread_decisions.py --strategy iron_condor --phase idle --symbol SPY
    python scripts/test_spread_decisions.py --strategy long_call_vertical --phase open
"""

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("test-spread")

_VALID_ACTIONS = {"OPEN", "SKIP", "HOLD", "CLOSE"}

_STRATEGIES = (
    "bull_put_spread",
    "bear_call_spread",
    "iron_condor",
    "long_call_vertical",
)

# ── Sample fallback context (used if live build fails) ──────────────
_SAMPLE_CONTEXT = {
    "timestamp": "2026-04-12T09:30:00",
    "symbol": "SPY",
    "wheel_state": "IDLE",
    "account": {
        "buying_power": 100000.0,
        "options_trading_level": 3,
        "portfolio_value": 200000.0,
    },
    "positions": [],
    "open_orders": [],
    "technicals": {
        "current_price": 495.00,
        "atr_14": 5.20,
        "rsi_14": 52.0,
        "sma_50": 488.00,
        "sma_200": 470.00,
        "above_sma_50": True,
        "above_sma_200": True,
    },
    "fundamentals": {
        "next_earnings_date": "2026-08-01",
        "days_to_earnings": 110,
        "sector": "ETF",
    },
    "macro": {
        "vix": 22.0,
        "vix_regime": "normal",
        "fear_greed_score": 55,
        "fear_greed_rating": "Neutral",
        "risk_free_rate": 0.0425,
    },
    "volatility": {
        "iv_rank_1y": 55.0,
        "iv_rank_1m": 60.0,
        "iv_environment": "HIGH",
        "implied_move_pct": 2.4,
        "orats_available": True,
    },
    "iv_rank": 55.0,
    "iv_environment": "HIGH",
    "confirmed_market_regime": "NEUTRAL",
    "regime_stable": True,
}


# ── Sample candidates in the *real* nested schema ───────────────────
# (shape produced by ContextBuilder._build_credit_spread_candidates and
# _build_debit_spread_candidates)

def _bull_put_candidates() -> dict:
    cands = [
        {
            "expiration": "2026-05-15",
            "dte": 33,
            "short_leg": {
                "symbol": "SPY260515P00485000",
                "strike": 485.0,
                "delta": -0.24,
                "bid": 1.10, "ask": 1.20, "mid": 1.15,
                "open_interest": 4500,
                "bid_ask_spread_pct": 8.7,
            },
            "long_leg": {
                "symbol": "SPY260515P00480000",
                "strike": 480.0,
                "delta": -0.18,
                "bid": 0.55, "ask": 0.65, "mid": 0.60,
                "open_interest": 3800,
            },
            "net_credit": 0.55,
            "max_loss": 445.0,
            "max_gain": 55.0,
            "break_even": 484.45,
            "credit_to_width_ratio": 0.11,
            "liquidity_ok": True,
        },
        {
            "expiration": "2026-05-15",
            "dte": 33,
            "short_leg": {
                "symbol": "SPY260515P00482000",
                "strike": 482.0,
                "delta": -0.20,
                "bid": 0.85, "ask": 0.95, "mid": 0.90,
                "open_interest": 2900,
                "bid_ask_spread_pct": 11.1,
            },
            "long_leg": {
                "symbol": "SPY260515P00477000",
                "strike": 477.0,
                "delta": -0.15,
                "bid": 0.40, "ask": 0.50, "mid": 0.45,
                "open_interest": 2400,
            },
            "net_credit": 0.45,
            "max_loss": 455.0,
            "max_gain": 45.0,
            "break_even": 481.55,
            "credit_to_width_ratio": 0.09,
            "liquidity_ok": True,
        },
    ]
    return {
        "underlying": "SPY",
        "underlying_price": 495.0,
        "strategy_type": "bull_put_spread",
        "candidates": cands,
        "best_candidate": cands[0],
    }


def _bear_call_candidates() -> dict:
    cands = [
        {
            "expiration": "2026-05-15",
            "dte": 33,
            "short_leg": {
                "symbol": "SPY260515C00505000",
                "strike": 505.0,
                "delta": 0.24,
                "bid": 1.05, "ask": 1.15, "mid": 1.10,
                "open_interest": 4100,
                "bid_ask_spread_pct": 9.1,
            },
            "long_leg": {
                "symbol": "SPY260515C00510000",
                "strike": 510.0,
                "delta": 0.17,
                "bid": 0.55, "ask": 0.65, "mid": 0.60,
                "open_interest": 3500,
            },
            "net_credit": 0.50,
            "max_loss": 450.0,
            "max_gain": 50.0,
            "break_even": 505.50,
            "credit_to_width_ratio": 0.10,
            "liquidity_ok": True,
        },
    ]
    return {
        "underlying": "SPY",
        "underlying_price": 495.0,
        "strategy_type": "bear_call_spread",
        "candidates": cands,
        "best_candidate": cands[0],
    }


def _iron_condor_candidates() -> dict:
    put_side = _bull_put_candidates()["best_candidate"]
    call_side = _bear_call_candidates()["best_candidate"]
    legs = {
        "put_spread": put_side,
        "call_spread": call_side,
        "total_credit": round(put_side["net_credit"] + call_side["net_credit"], 4),
        "total_max_loss": max(put_side["max_loss"], call_side["max_loss"]),
    }
    return {
        "underlying": "SPY",
        "underlying_price": 495.0,
        "strategy_type": "iron_condor",
        "candidates": [put_side, call_side],
        "best_candidate": legs,
        "iron_condor_legs": legs,
    }


def _long_call_vertical_candidates() -> dict:
    cands = [
        {
            "expiration": "2026-05-22",
            "dte": 40,
            "short_leg": {
                "symbol": "SPY260522C00505000",
                "strike": 505.0,
                "delta": 0.32,
                "bid": 2.10, "ask": 2.25, "mid": 2.18,
                "open_interest": 3200,
                "bid_ask_spread_pct": 6.9,
            },
            "long_leg": {
                "symbol": "SPY260522C00500000",
                "strike": 500.0,
                "delta": 0.48,
                "bid": 3.40, "ask": 3.55, "mid": 3.48,
                "open_interest": 4100,
            },
            "net_debit": 1.30,
            "max_loss": 130.0,
            "max_gain": 370.0,
            "break_even": 501.30,
            "credit_to_width_ratio": 0.74,
            "liquidity_ok": True,
        },
    ]
    return {
        "underlying": "SPY",
        "underlying_price": 495.0,
        "strategy_type": "long_call_vertical",
        "candidates": cands,
        "best_candidate": cands[0],
    }


_CANDIDATE_BUILDERS = {
    "bull_put_spread": _bull_put_candidates,
    "bear_call_spread": _bear_call_candidates,
    "iron_condor": _iron_condor_candidates,
    "long_call_vertical": _long_call_vertical_candidates,
}


def _open_spread_state(strategy: str) -> dict:
    """Sample 'open' position data for management-phase prompts."""
    base = {
        "strategy_type": strategy,
        "underlying": "SPY",
        "expiration": "2026-04-26",
        "dte_remaining": 14,
        "original_credit": 1.15,
        "current_spread_value": 0.55,
        "pnl_pct": 52.2,
    }
    if strategy == "iron_condor":
        base.update({
            "put_short_symbol": "SPY260426P00485000",
            "put_long_symbol": "SPY260426P00480000",
            "call_short_symbol": "SPY260426C00505000",
            "call_long_symbol": "SPY260426C00510000",
            "put_short_current_delta": -0.31,
            "call_short_current_delta": 0.22,
        })
    elif strategy == "long_call_vertical":
        base.update({
            "long_symbol": "SPY260426C00500000",
            "short_symbol": "SPY260426C00505000",
            "original_debit": 1.30,
            "current_spread_value": 2.10,
            "pnl_pct": 61.5,
            "long_current_delta": 0.62,
        })
    else:
        base.update({
            "short_symbol": "SPY260426P00485000",
            "long_symbol": "SPY260426P00480000",
            "short_current_delta": -0.31,
        })
    return base


# ── Live context builder ────────────────────────────────────────────

def _build_live_context(symbol: str) -> dict | None:
    try:
        from brokers.alpaca_broker import AlpacaBroker
        from data.context_builder import ContextBuilder

        broker = AlpacaBroker()
        builder = ContextBuilder(broker)
        return builder.build(symbol, "IDLE")
    except Exception:
        logger.warning("ContextBuilder failed — falling back to sample context", exc_info=True)
        return None


# ── Guardrail wiring test ───────────────────────────────────────────

def _run_guardrail_test() -> bool:
    """Confirm validate_bull_put_spread_entry rejects a missing short_put_symbol."""
    from strategies.guardrails import Guardrails

    g = Guardrails()
    decision = {
        "action": "OPEN",
        "limit_price": -0.55,        # negative = credit
        "net_credit": 0.55,
        "max_loss": 445.0,
        "dte": 30,
        # short_put_symbol intentionally omitted
        "long_put_symbol": "SPY260515P00480000",
    }
    context = {"fundamentals": {"days_to_earnings": 90}}
    account = {"buying_power": 100_000.0}

    is_valid, rejection = g.validate_bull_put_spread_entry(
        decision, context, account, open_spreads=[],
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("  Guardrail wiring test")
    logger.info("=" * 60)
    logger.info("  is_valid: %s", is_valid)
    logger.info("  rejection: %s", rejection)

    if not is_valid and isinstance(rejection, str) and rejection:
        logger.info("Guardrail test: PASS")
        return True
    logger.error("Guardrail test: FAIL — expected (False, <reason>) for missing short_put_symbol")
    return False


# ── Main flow ───────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Test Claude spread decision flow")
    parser.add_argument("--strategy", required=True, choices=_STRATEGIES)
    parser.add_argument("--phase", required=True, choices=("idle", "open"))
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument(
        "--sample", action="store_true",
        help="Force sample context (skip live ContextBuilder)",
    )
    args = parser.parse_args()

    from ai.claude_advisor import ClaudeAdvisor

    strategy = args.strategy
    phase = args.phase
    symbol = args.symbol.upper()

    # ── Build context ───────────────────────────────────────
    logger.info("Building context for %s (%s, phase=%s)...", symbol, strategy, phase)

    context = None
    if not args.sample:
        context = _build_live_context(symbol)
    if context is None:
        logger.info("Using sample context")
        context = json.loads(json.dumps(_SAMPLE_CONTEXT))  # deep-ish copy
        context["symbol"] = symbol

    # ── Inject candidates or open-spread state ──────────────
    if phase == "idle":
        candidates = _CANDIDATE_BUILDERS[strategy]()
        context.setdefault("spread_candidates", {})[strategy] = candidates
        # Strategy entry paths read from spread_candidates[<name>], but the
        # ask_spread() prompt also tends to read top-level best_candidate.
        context["best_candidate"] = candidates.get("best_candidate")
        if strategy == "iron_condor":
            context["iron_condor_candidate"] = candidates.get("iron_condor_legs")
    else:
        context["open_spread"] = _open_spread_state(strategy)

    # ── Call Claude ─────────────────────────────────────────
    advisor = ClaudeAdvisor()

    logger.info("")
    logger.info("=" * 60)
    logger.info("  Sending to Claude (%s)", advisor.model)
    logger.info("  strategy=%s phase=%s", strategy, phase)
    logger.info("=" * 60)

    t0 = time.monotonic()
    decision: dict = {}
    error: str | None = None
    try:
        decision = advisor.ask_spread(context, strategy, phase)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("ask_spread raised")
    elapsed = time.monotonic() - t0

    # ── Full JSON ───────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("  Full Decision JSON")
    logger.info("=" * 60)
    print(json.dumps(decision, indent=2, default=str))

    # ── Summary ─────────────────────────────────────────────
    action = str(decision.get("action", "?")).upper()
    confidence = decision.get("confidence", "?")
    limit = decision.get("limit_price")
    limit_str = f"{limit}" if limit is not None else "N/A"

    logger.info("")
    logger.info("=" * 60)
    logger.info("  Summary")
    logger.info("=" * 60)
    logger.info("  Action:     %s", action)
    logger.info("  Confidence: %s", confidence)
    logger.info("  Limit:      %s", limit_str)

    # OCC symbols
    occ_keys = (
        "short_symbol", "long_symbol",
        "short_put_symbol", "long_put_symbol",
        "short_call_symbol", "long_call_symbol",
    )
    occ_present = {k: decision[k] for k in occ_keys if decision.get(k)}
    if occ_present:
        logger.info("  OCC symbols:")
        for k, v in occ_present.items():
            logger.info("    %-20s %s", f"{k}:", v)

    reasoning = decision.get("reasoning")
    if isinstance(reasoning, dict):
        logger.info("  Reasoning:")
        for key, value in reasoning.items():
            logger.info("    %-14s %s", f"{key}:", value)
    elif reasoning:
        logger.info("  Reasoning:  %s", reasoning)

    if decision.get("skip_reason"):
        logger.info("  Skip reason: %s", decision["skip_reason"])

    # Cache stats
    cache = advisor.prompt_cache_stats
    logger.info("")
    logger.info("=" * 60)
    logger.info("  API Stats")
    logger.info("=" * 60)
    logger.info("  Response time: %.2fs", elapsed)
    if cache:
        logger.info("  Input tokens:          %s", cache.get("input_tokens", "?"))
        logger.info("  Output tokens:         %s", cache.get("output_tokens", "?"))
        logger.info("  Cache read tokens:     %s", cache.get("cache_read_input_tokens", 0))
        logger.info("  Cache creation tokens: %s", cache.get("cache_creation_input_tokens", 0))
    else:
        logger.info("  Token usage: unavailable")

    # ── PASS / FAIL ─────────────────────────────────────────
    advisor_pass = (
        error is None
        and isinstance(decision, dict)
        and "action" in decision
        and action in _VALID_ACTIONS
    )
    logger.info("")
    logger.info("=" * 60)
    logger.info("  Advisor decision test: %s", "PASS" if advisor_pass else "FAIL")
    if error:
        logger.info("    Error: %s", error)
    elif not advisor_pass:
        logger.info("    Reason: action=%r missing or not in %s", action, sorted(_VALID_ACTIONS))
    logger.info("=" * 60)

    # ── Guardrail wiring test ──────────────────────────────
    guardrail_pass = _run_guardrail_test()

    # ── Final result ───────────────────────────────────────
    overall = advisor_pass and guardrail_pass
    logger.info("")
    logger.info("=" * 60)
    logger.info("  OVERALL: %s", "PASS" if overall else "FAIL")
    logger.info("=" * 60)
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
