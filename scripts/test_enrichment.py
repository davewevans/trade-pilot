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
TOTAL = 9


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

    # ── 4. Company profile ──────────────────────────────────
    section(4, f"get_company_profile('{SYMBOL}')")
    try:
        profile = market_data.get_company_profile(SYMBOL)
        logger.info("  Sector:           %s", profile.get("sector"))
        logger.info("  Market cap (M):   %s", profile.get("market_cap"))
        logger.info("  PE ratio:         %s", profile.get("pe_ratio"))
        logger.info("  Div yield:        %s", profile.get("annual_dividend_yield"))
        logger.info("  52w high:         %s", profile.get("fifty_two_week_high"))
        logger.info("  52w low:          %s", profile.get("fifty_two_week_low"))
        logger.info("  Is ETF:           %s", profile.get("is_etf"))
        logger.info("  Profile avail:    %s", profile.get("profile_data_available"))
        logger.info("  Metric avail:     %s", profile.get("metric_data_available"))
        if profile.get("sector") is not None and profile.get("fifty_two_week_high") is not None:
            passed("get_company_profile")
        else:
            failed("get_company_profile", "sector or 52w high is None — Finnhub may be down")
    except Exception as e:
        failed("get_company_profile", str(e))
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

    # ── 7. ORATS Summary ────────────────────────────────────
    section(7, f"get_orats_summary('{SYMBOL}')")
    try:
        from data.market_data import get_orats_summary
        summary = get_orats_summary(SYMBOL)
        if summary is not None:
            logger.info("  IV Rank (1y):       %.1f", summary.get("iv_rank_1y") or 0)
            logger.info("  IV Percentile (1y): %.1f", summary.get("iv_pct_1y") or 0)
            from data.orats_client import ORATSClient
            logger.info("  IV Environment:     %s",
                        ORATSClient.classify_iv_environment(summary.get("iv_rank_1y")))
            logger.info("  ATM IV M1:          %.3f", summary.get("atm_iv_m1") or 0)
            logger.info("  ATM IV M2:          %.3f", summary.get("atm_iv_m2") or 0)
            logger.info("  Term slope M1-M2:   %.4f", summary.get("term_structure_slope") or 0)
            logger.info("  Skew M1:            %.4f", summary.get("skew_m1") or 0)
            logger.info("  Implied move:       %.2f%%", summary.get("implied_move_pct") or 0)
            if summary.get("iv_rank_1y") is not None:
                passed("get_orats_summary")
            else:
                failed("get_orats_summary", "iv_rank_1y is None")
        else:
            failed("get_orats_summary", "returned None -- check ORATS_API_KEY in .env")
    except Exception as e:
        failed("get_orats_summary", str(e))
        logger.exception("  Details:")

    # ── 8. Finnhub Earnings ──────────────────────────────────
    section(8, f"get_earnings_calendar('{SYMBOL}')")
    try:
        from data.market_data import get_earnings_calendar
        ec = get_earnings_calendar(SYMBOL)
        logger.info("  Source:          %s", ec.get("source"))
        logger.info("  Earnings date:   %s", ec.get("next_earnings_date"))
        logger.info("  Days to earn:    %s", ec.get("days_to_earnings"))
        logger.info("  EPS estimate:    %s", ec.get("eps_estimate"))
        if ec.get("source") != "unavailable":
            passed("get_earnings_calendar")
        else:
            failed("get_earnings_calendar", "both Finnhub and yfinance returned nothing")
    except Exception as e:
        failed("get_earnings_calendar", str(e))
        logger.exception("  Details:")

    # ── 9. Ex-Dividend Date ─────────────────────────────────
    section(9, f"get_ex_dividend_date('{SYMBOL}')")
    try:
        from data.market_data import get_ex_dividend_date
        from config import settings as _settings
        exdiv = get_ex_dividend_date(SYMBOL)
        logger.info("  (sourced via %s)", "Alpaca" if _settings.USE_ALPACA_FOR_EX_DIVIDEND else "yfinance")
        logger.info("  Ex-div date:  %s", exdiv.get("next_ex_dividend_date"))
        logger.info("  Days to ex:   %s", exdiv.get("days_to_ex_dividend"))
        logger.info("  Div yield:    %s", exdiv.get("annual_dividend_yield"))
        passed("get_ex_dividend_date")
    except Exception as e:
        failed("get_ex_dividend_date", str(e))
        logger.exception("  Details:")

    # ── Summary ─────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("  RESULTS: %d/%d data sources operational", PASS_COUNT, TOTAL)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
