"""Test the Claude advisor against real or sample context without executing trades.

Usage:
    python scripts/test_advisor.py
    python scripts/test_advisor.py --symbol SPY --phase idle
    python scripts/test_advisor.py --phase short_put
"""

import argparse
import json
import logging
import sys
import os
import time

# Allow running from the scripts/ directory or project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("test-advisor")

_PHASE_MAP = {
    "idle": "IDLE",
    "short_put": "SHORT_PUT",
    "long_stock": "LONG_STOCK",
    "short_call": "SHORT_CALL",
}

_SAMPLE_CONTEXT = {
    "timestamp": "2026-04-10T09:30:00",
    "symbol": "AAPL",
    "wheel_state": "IDLE",
    "account": {
        "buying_power": 100000.0,
        "options_trading_level": 3,
        "portfolio_value": 200000.0,
    },
    "positions": [],
    "open_orders": [],
    "technicals": {
        "current_price": 260.48,
        "atr_14": 6.10,
        "rsi_14": 55.3,
        "sma_20": 253.29,
        "bollinger_upper": 261.48,
        "bollinger_lower": 245.09,
        "price_change_pct_30d": -1.4,
        "sma_50": 260.88,
        "sma_200": 250.63,
        "above_sma_50": False,
        "above_sma_200": True,
        "golden_cross": True,
        "macd_value": -0.17,
        "macd_signal": -1.64,
        "macd_bullish": True,
        "avg_volume_10d": 40178734,
        "avg_volume_30d": 41348028,
        "volume_trend": "decreasing",
        "price_change_pct_5d": 1.78,
        "price_change_pct_20d": 1.85,
    },
    "fundamentals": {
        "next_earnings_date": "2026-07-24",
        "days_to_earnings": 105,
        "pe_ratio": 33.01,
        "market_cap": 3828000000000,
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "avg_volume": 47317286,
        "fifty_two_week_high": 288.62,
        "fifty_two_week_low": 186.06,
        "analyst_rating": None,
    },
    "macro": {
        "vix": 22.5,
        "vix_regime": "normal",
        "fear_greed_score": 55.0,
        "fear_greed_rating": "Neutral",
        "risk_free_rate": 0.0368,
    },
    "option_chain": {
        "contracts": [
            {
                "symbol": "AAPL260501P00250000",
                "name": "AAPL May 01 2026 250 Put",
                "strike_price": "250",
                "expiration_date": "2026-05-01",
                "dte": 21,
                "type": "put",
                "open_interest": "1250",
                "close_price": "3.40",
                "tradable": True,
            },
            {
                "symbol": "AAPL260501P00252500",
                "name": "AAPL May 01 2026 252.5 Put",
                "strike_price": "252.5",
                "expiration_date": "2026-05-01",
                "dte": 21,
                "type": "put",
                "open_interest": "890",
                "close_price": "4.10",
                "tradable": True,
            },
            {
                "symbol": "AAPL260508P00250000",
                "name": "AAPL May 08 2026 250 Put",
                "strike_price": "250",
                "expiration_date": "2026-05-08",
                "dte": 28,
                "type": "put",
                "open_interest": "2100",
                "close_price": "4.85",
                "tradable": True,
            },
        ],
        "snapshots": {
            "AAPL260501P00250000": {
                "symbol": "AAPL260501P00250000",
                "implied_volatility": 0.28,
                "latest_quote": {
                    "bid_price": 3.25,
                    "bid_size": 45,
                    "ask_price": 3.40,
                    "ask_size": 50,
                    "timestamp": "2026-04-10T15:59:00",
                },
                "latest_trade": {"price": 3.30, "size": 5, "timestamp": "2026-04-10T15:58:00"},
                "greeks": {"delta": -0.24, "gamma": 0.012, "theta": -0.15, "vega": 0.30, "rho": -0.04},
            },
            "AAPL260501P00252500": {
                "symbol": "AAPL260501P00252500",
                "implied_volatility": 0.27,
                "latest_quote": {
                    "bid_price": 3.95,
                    "bid_size": 30,
                    "ask_price": 4.10,
                    "ask_size": 35,
                    "timestamp": "2026-04-10T15:59:00",
                },
                "latest_trade": {"price": 4.00, "size": 3, "timestamp": "2026-04-10T15:57:00"},
                "greeks": {"delta": -0.28, "gamma": 0.013, "theta": -0.16, "vega": 0.32, "rho": -0.05},
            },
            "AAPL260508P00250000": {
                "symbol": "AAPL260508P00250000",
                "implied_volatility": 0.29,
                "latest_quote": {
                    "bid_price": 4.70,
                    "bid_size": 60,
                    "ask_price": 4.90,
                    "ask_size": 55,
                    "timestamp": "2026-04-10T15:59:00",
                },
                "latest_trade": {"price": 4.80, "size": 10, "timestamp": "2026-04-10T15:58:30"},
                "greeks": {"delta": -0.26, "gamma": 0.011, "theta": -0.13, "vega": 0.35, "rho": -0.06},
            },
        },
    },
    "news": [
        {"headline": "Apple Reports Strong Services Revenue Growth", "source": "Reuters", "url": "#", "created_at": "2026-04-10T12:00:00"},
        {"headline": "Tech Stocks Rally on Trade Deal Optimism", "source": "CNBC", "url": "#", "created_at": "2026-04-10T10:30:00"},
    ],
}


def _build_live_context(symbol: str, wheel_state: str) -> dict | None:
    """Try to build a real context via ContextBuilder. Returns None on failure."""
    try:
        from brokers.alpaca_broker import AlpacaBroker
        from data.context_builder import ContextBuilder

        broker = AlpacaBroker()
        builder = ContextBuilder(broker)
        return builder.build(symbol, wheel_state)
    except Exception:
        logger.warning("ContextBuilder failed — falling back to sample context", exc_info=True)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Test the Claude advisor")
    parser.add_argument(
        "--symbol", default="AAPL",
        help="Underlying symbol to test (default: AAPL)",
    )
    parser.add_argument(
        "--phase", default="idle",
        choices=list(_PHASE_MAP.keys()),
        help="Wheel phase to test (default: idle)",
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Force use of sample context (skip live data fetch)",
    )
    args = parser.parse_args()

    from strategies.wheel_strategy import WheelState
    from ai.claude_advisor import ClaudeAdvisor

    phase = WheelState(args.phase.upper() if args.phase.upper() in _PHASE_MAP.values() else _PHASE_MAP[args.phase])
    symbol = args.symbol.upper()

    # ── Build context ───────────────────────────────────────
    logger.info("Building context for %s (phase=%s)...", symbol, phase.value)

    context = None
    if not args.sample:
        context = _build_live_context(symbol, phase.value)

    if context is None:
        logger.info("Using sample context")
        context = _SAMPLE_CONTEXT.copy()
        context["symbol"] = symbol
        context["wheel_state"] = phase.value

    # ── Call Claude ──────────────────────────────────────────
    advisor = ClaudeAdvisor()

    logger.info("")
    logger.info("=" * 60)
    logger.info("  Sending to Claude (%s)", advisor.model)
    logger.info("=" * 60)

    t0 = time.monotonic()
    try:
        decision = advisor.ask(context, phase)
    except ValueError as e:
        logger.error("Advisor returned an invalid response: %s", e)
        return
    elapsed = time.monotonic() - t0

    # ── Full JSON ───────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("  Full Decision JSON")
    logger.info("=" * 60)
    print(json.dumps(decision, indent=2))

    # ── Human-readable summary ──────────────────────────────
    action = decision.get("action", "?").upper()
    sym = decision.get("symbol") or "N/A"
    limit = decision.get("limit_price")
    limit_str = f"${limit:.2f}" if limit is not None else "N/A"
    confidence = decision.get("confidence", "?")

    logger.info("")
    logger.info("=" * 60)
    logger.info("  Summary")
    logger.info("=" * 60)
    logger.info(
        "Decision: %s | Symbol: %s | Limit: %s | Confidence: %s",
        action, sym, limit_str, confidence,
    )

    # ── Reasoning breakdown ─────────────────────────────────
    reasoning = decision.get("reasoning", {})
    if reasoning:
        logger.info("")
        logger.info("=" * 60)
        logger.info("  Reasoning")
        logger.info("=" * 60)
        for key, value in reasoning.items():
            logger.info("  %-14s %s", f"{key}:", value)

    skip_reason = decision.get("skip_reason")
    if skip_reason:
        logger.info("  %-14s %s", "skip_reason:", skip_reason)

    # ── Cache stats ─────────────────────────────────────────
    cache = advisor.prompt_cache_stats
    logger.info("")
    logger.info("=" * 60)
    logger.info("  API Stats")
    logger.info("=" * 60)
    logger.info("  Response time: %.2fs", elapsed)
    if cache:
        logger.info("  Input tokens:          %s", cache.get("input_tokens", "?"))
        logger.info("  Output tokens:         %s", cache.get("output_tokens", "?"))
        cache_read = cache.get("cache_read_input_tokens", 0)
        cache_create = cache.get("cache_creation_input_tokens", 0)
        if cache_read or cache_create:
            logger.info("  Cache read tokens:     %s", cache_read)
            logger.info("  Cache creation tokens: %s", cache_create)
        else:
            logger.info("  Cache: no cache hits this request")
    else:
        logger.info("  Token usage: unavailable")


if __name__ == "__main__":
    main()
