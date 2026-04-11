"""Integration test for all enrichment data sources."""

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
logger = logging.getLogger("test-enrichment")

SYMBOL = "AAPL"
PASS_COUNT = 0
TOTAL = 6


def section(num: int, title: str) -> None:
    logger.info("")
    logger.info("=" * 60)
    logger.info("  TEST %d/%d: %s", num, TOTAL, title)
    logger.info("=" * 60)


def passed(label: str) -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    logger.info("[PASS] %s", label)


def failed(label: str, reason: str) -> None:
    logger.error("[FAIL] %s — %s", label, reason)


def main() -> None:
    from data import market_data
    from brokers.alpaca_broker import AlpacaBroker
    from data.context_builder import ContextBuilder

    # ── 1. Risk-free rate ───────────────────────────────────
    section(1, "get_risk_free_rate()")
    try:
        rate = market_data.get_risk_free_rate()
        logger.info("  Risk-free rate: %.4f (%.2f%%)", rate, rate * 100)
        if 0.0 <= rate <= 0.15:
            passed("get_risk_free_rate")
        else:
            failed("get_risk_free_rate", f"rate {rate} outside expected range 0.0–0.15")
    except Exception as e:
        failed("get_risk_free_rate", str(e))
        logger.exception("  Details:")

    # ── 2. Fear & Greed Index ───────────────────────────────
    section(2, "get_fear_greed_index()")
    try:
        fg = market_data.get_fear_greed_index()
        logger.info("  Score:  %s", fg.get("score"))
        logger.info("  Rating: %s", fg.get("rating"))
        score = fg.get("score")
        if score is not None and 0 <= score <= 100:
            passed("get_fear_greed_index")
        elif score is None and fg.get("rating") == "Unknown":
            failed("get_fear_greed_index", "API returned fallback (score=None)")
        else:
            failed("get_fear_greed_index", f"score {score} outside expected range 0–100")
    except Exception as e:
        failed("get_fear_greed_index", str(e))
        logger.exception("  Details:")

    # ── 3. VIX proxy ────────────────────────────────────────
    section(3, "get_vix()")
    try:
        vix = market_data.get_vix()
        if vix is not None:
            regime = market_data.interpret_vix(vix)
            logger.info("  VIXY price: %.2f", vix)
            logger.info("  Regime:     %s", regime)
            passed("get_vix")
        else:
            failed("get_vix", "returned None")
    except Exception as e:
        failed("get_vix", str(e))
        logger.exception("  Details:")

    # ── 4. Fundamentals ─────────────────────────────────────
    section(4, f"get_fundamentals('{SYMBOL}')")
    try:
        fund = market_data.get_fundamentals(SYMBOL)
        logger.info("  Earnings date:    %s", fund.get("next_earnings_date"))
        logger.info("  Days to earnings: %s", fund.get("days_to_earnings"))
        logger.info("  Sector:           %s", fund.get("sector"))
        logger.info("  PE ratio:         %s", fund.get("pe_ratio"))
        # Pass if we got at least sector (yfinance sometimes returns partial data)
        if fund.get("sector") is not None:
            passed("get_fundamentals")
        else:
            failed("get_fundamentals", "sector is None — yfinance may be down")
    except Exception as e:
        failed("get_fundamentals", str(e))
        logger.exception("  Details:")

    # ── 5. Stock technicals ─────────────────────────────────
    section(5, f"get_stock_technicals('{SYMBOL}')")
    try:
        tech = market_data.get_stock_technicals(SYMBOL)
        logger.info("  Current price: %s", tech.get("current_price"))
        logger.info("  RSI-14:        %s", tech.get("rsi_14"))
        logger.info("  SMA-50:        %s", tech.get("sma_50"))
        logger.info("  SMA-200:       %s", tech.get("sma_200"))
        logger.info("  Above SMA-50:  %s", tech.get("above_sma_50"))
        logger.info("  Above SMA-200: %s", tech.get("above_sma_200"))
        logger.info("  Golden cross:  %s", tech.get("golden_cross"))
        logger.info("  MACD bullish:  %s", tech.get("macd_bullish"))
        logger.info("  Volume trend:  %s", tech.get("volume_trend"))
        if tech.get("current_price") is not None and tech.get("sma_50") is not None:
            passed("get_stock_technicals")
        else:
            failed("get_stock_technicals", "missing current_price or sma_50")
    except Exception as e:
        failed("get_stock_technicals", str(e))
        logger.exception("  Details:")

    # ── 6. ContextBuilder ───────────────────────────────────
    section(6, f"ContextBuilder.build('{SYMBOL}', 'IDLE')")
    try:
        broker = AlpacaBroker()
        builder = ContextBuilder(broker)

        t0 = time.monotonic()
        context = builder.build(SYMBOL, "IDLE")
        elapsed = time.monotonic() - t0

        logger.info("  Full context JSON:")
        print(json.dumps(context, indent=2, default=str))

        summary = builder.summarize_for_log(context)
        logger.info("")
        logger.info("  Log summary: %s", summary)
        logger.info("  Build time:  %.2fs", elapsed)

        if context.get("technicals") is not None and context.get("macro") is not None:
            passed("ContextBuilder.build")
        else:
            failed("ContextBuilder.build", "technicals or macro is None")
    except Exception as e:
        failed("ContextBuilder.build", str(e))
        logger.exception("  Details:")

    # ── Summary ─────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("  RESULTS: %d/%d data sources operational", PASS_COUNT, TOTAL)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
