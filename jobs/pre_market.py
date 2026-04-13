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

    # Reset daily success/failure counters at the start of each trading day.
    try:
        from data.source_health import SourceHealth
        SourceHealth().reset_daily_counts()
        logger.info("Source health daily counts reset")
    except Exception:
        logger.warning("Failed to reset source health daily counts", exc_info=True)

    from brokers.broker_factory import get_broker
    from data.context_builder import ContextBuilder
    from data.state_writer import StateWriter
    from data.trade_journal import TradeJournal

    broker = get_broker()
    journal = TradeJournal(path=settings.JOURNAL_PATH)
    ctx_builder = ContextBuilder(broker=broker, journal=journal)
    sw = StateWriter()

    # ── Check for overnight option events ──────────────────
    try:
        option_events = broker.get_account_activities(
            ["OPASN", "OPEXP", "OPEXC", "OPTRD"]
        )
    except Exception:
        logger.exception("Failed to fetch overnight option events")
        option_events = []

    if option_events:
        logger.info("=== OVERNIGHT OPTION EVENTS ===")
        for evt in option_events:
            logger.info(
                "  %s | %s | qty=%s | net=%s",
                evt.get("activity_type"),
                evt.get("symbol"),
                evt.get("qty"),
                evt.get("net_amount"),
            )
        try:
            sw.write_option_events(option_events)
        except Exception as e:
            logger.warning("Failed to write option events: %s", e)
    else:
        logger.info("No overnight option events found")

    # ── Cancel any open option orders from prior sessions ──
    try:
        open_orders = broker.get_orders(status="open")
        option_orders = [
            o for o in open_orders
            if o.get("asset_class") == "us_option"
        ]
        if option_orders:
            logger.info(
                "Found %d stale open option orders — canceling",
                len(option_orders),
            )
            for order in option_orders:
                try:
                    broker.cancel_order(order["id"])
                    logger.info(
                        "Canceled stale order %s (%s)",
                        order["id"], order.get("symbol"),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to cancel order %s: %s",
                        order.get("id"), e,
                    )
        else:
            logger.info("No stale open option orders found")
    except Exception:
        logger.warning("Failed to check/cancel stale orders", exc_info=True)

    # ── Batch IV rank screen (one ORATS call) ───────────────
    briefing_lines: list[str] = []
    iv_ranks: dict[str, dict] = {}
    try:
        iv_ranks = market_data.get_orats_iv_rank_batch(list(settings.WATCHLIST))
    except Exception:
        logger.exception("Batch IV rank screen failed")

    if iv_ranks:
        briefing_lines.append("**IV Rank screen (ORATS):**")
        for sym in settings.WATCHLIST:
            entry = iv_ranks.get(sym.upper(), {})
            ivr1y = entry.get("ivRank1y")
            ivr1m = entry.get("ivRank1m")
            qual = "qualifies" if (ivr1y is not None and ivr1y >= 25) else "below threshold"
            line = (
                f"- {sym}: ivRank1y={ivr1y if ivr1y is not None else 'N/A'} "
                f"ivRank1m={ivr1m if ivr1m is not None else 'N/A'} ({qual})"
            )
            logger.info(line)
            briefing_lines.append(line)
        briefing_lines.append("")

    # ── Per-symbol fundamentals & news ──────────────────────

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

    # Prune old terminal spreads from the tracker (prevents unbounded growth).
    try:
        from data.spread_tracker import SpreadTracker
        pruned = SpreadTracker().prune_old_spreads(max_age_days=30)
        if pruned:
            logger.info("Pruned %d old spread tracker entries", pruned)
    except Exception:
        logger.warning("Failed to prune spread tracker", exc_info=True)

    logger.info("=== PRE-MARKET JOB COMPLETE ===")
