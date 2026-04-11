"""Pre-market job — runs at 6:00 AM ET every weekday."""

import logging

from config import settings
from data import market_data
from data.context_builder import _fetch_news
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Fetch fundamentals, news, and macro data for each watchlist symbol.

    Writes a pre-market briefing section to today's daily report.
    """
    logger.info("=== PRE-MARKET JOB STARTING ===")

    from brokers.broker_factory import get_broker
    from data.context_builder import ContextBuilder
    from data.trade_journal import TradeJournal

    broker = get_broker()
    journal = TradeJournal(path=settings.JOURNAL_PATH)
    ctx_builder = ContextBuilder(broker=broker, journal=journal)

    # ── Per-symbol fundamentals & news ──────────────────────
    briefing_lines: list[str] = []

    for symbol in settings.WATCHLIST:
        try:
            fundamentals = market_data.get_fundamentals(symbol)
            news = _fetch_news(symbol)

            dte = fundamentals.get("days_to_earnings")
            dte_str = f"{dte}d away" if dte is not None else "N/A"

            technicals = market_data.get_stock_technicals(symbol)
            rsi = technicals.get("rsi_14")
            if rsi is not None:
                if rsi > 60:
                    trend = "bullish"
                elif rsi < 40:
                    trend = "bearish"
                else:
                    trend = "neutral"
            else:
                trend = "unknown"

            news_count = len(news) if news else 0
            summary = f"{symbol} | Earnings: {dte_str} | Trend: {trend} | News: {news_count} headlines"
            logger.info(summary)
            briefing_lines.append(summary)
        except Exception:
            logger.exception("Pre-market failed for %s — continuing", symbol)
            briefing_lines.append(f"{symbol} | ERROR — see logs")

    # ── Macro data (once) ───────────────────────────────────
    try:
        vix = market_data.get_vix()
        vix_regime = market_data.interpret_vix(vix) if vix is not None else "?"
        fg = market_data.get_fear_greed_index() or {}
        rfr = market_data.get_risk_free_rate()

        vix_str = f"{vix:.1f} ({vix_regime})" if vix is not None else "N/A"
        fg_str = f"{fg.get('score', 0):.0f} ({fg.get('rating', '?')})" if fg.get("score") else "N/A"
        rfr_str = f"{rfr * 100:.2f}%" if rfr is not None else "N/A"

        macro_line = f"Macro: VIX={vix_str} | F&G={fg_str} | RFR={rfr_str}"
        logger.info(macro_line)
        briefing_lines.append("")
        briefing_lines.append(macro_line)
    except Exception:
        logger.exception("Failed to fetch macro data")
        briefing_lines.append("Macro: ERROR — see logs")

    # ── Write daily report section ──────────────────────────
    append_section("Pre-Market Briefing (6:00 AM ET)", "\n".join(briefing_lines))

    logger.info("=== PRE-MARKET JOB COMPLETE ===")
