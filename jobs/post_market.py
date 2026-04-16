"""Post-market job — runs at 4:30 PM ET every weekday."""

import json
import logging
from datetime import datetime

from config import settings
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Poll for option expirations, assignments, and exercises. Update wheel state."""
    logger.info("=== POST-MARKET JOB STARTING ===")

    from brokers.broker_factory import get_broker
    from data.trade_journal import TradeJournal
    from strategies.wheel_strategy import WheelStrategy

    broker = get_broker()
    journal = TradeJournal(path=settings.JOURNAL_PATH)
    strategy = WheelStrategy(broker)

    today_str = datetime.now().strftime("%Y-%m-%d")
    report_lines: list[str] = []

    # ── Poll for non-trade activities ───────────────────────
    for activity_type in ("OEXP", "OASGN", "OEXC"):
        try:
            activities = broker.get_account_activities([activity_type])
        except Exception:
            logger.exception("Failed to fetch %s activities", activity_type)
            continue

        for act in activities:
            # Filter to today
            act_date = str(act.get("date", act.get("transaction_time", "")))
            if today_str not in act_date:
                continue

            symbol = act.get("symbol", "")
            qty = act.get("qty", "")
            price = act.get("price", "")

            # Extract underlying root from OCC symbol
            underlying = ""
            for ch in symbol:
                if ch.isalpha():
                    underlying += ch
                else:
                    break
            underlying = underlying.upper() or symbol

            # Determine option type (C/P) from OCC symbol
            option_type = None
            digits_start = None
            for i, ch in enumerate(symbol):
                if ch.isdigit():
                    digits_start = i
                    break
            if digits_start and len(symbol) > digits_start + 6:
                option_type = symbol[digits_start + 6].upper()

            if activity_type == "OASGN":
                msg = f"ASSIGNMENT: Bought {qty} shares of {underlying} @ ${price}"
                logger.info(msg)
                report_lines.append(msg)

                # Transition to LONG_STOCK
                state = strategy.get_current_state(underlying)
                logger.info("%s state after assignment: %s", underlying, state.value)

                journal.update(act.get("order_id", ""), {
                    "status": "assigned",
                    "closed_at": datetime.now().isoformat(timespec="seconds"),
                })

            elif activity_type == "OEXP":
                msg = f"EXPIRY: {symbol} expired worthless — full premium kept"
                logger.info(msg)
                report_lines.append(msg)

                # Put expiry → IDLE, call expiry → LONG_STOCK (keep shares)
                state = strategy.get_current_state(underlying)
                logger.info("%s state after expiry: %s", underlying, state.value)

                journal.update(act.get("order_id", ""), {
                    "status": "expired",
                    "closed_at": datetime.now().isoformat(timespec="seconds"),
                })

            elif activity_type == "OEXC":
                msg = f"EXERCISE: {symbol} exercised (qty={qty}, price=${price})"
                logger.info(msg)
                report_lines.append(msg)

                state = strategy.get_current_state(underlying)
                logger.info("%s state after exercise: %s", underlying, state.value)

                journal.update(act.get("order_id", ""), {
                    "status": "exercised",
                    "closed_at": datetime.now().isoformat(timespec="seconds"),
                })

    strategy.save_state()

    # ── Refresh portfolio patterns (daily) ─────────────────
    # Context builder reads this file on every Claude API call; writing it
    # daily keeps the data fresh. weekly_report.py also writes it on Sunday —
    # whichever runs last wins, no race condition since both produce identical
    # content from the same journal source.
    try:
        patterns = journal.get_portfolio_patterns(days=30)
        patterns_path = settings.SNAPSHOTS_DIR / "portfolio_patterns.json"
        patterns_path.parent.mkdir(parents=True, exist_ok=True)
        patterns_path.write_text(json.dumps(patterns, indent=2), encoding="utf-8")
        logger.info("Portfolio patterns refreshed: %s", patterns_path)
    except Exception:
        logger.exception("Failed to refresh portfolio patterns")

    # ── Finalize daily report ───────────────────────────────
    if report_lines:
        append_section("Post-Market Events (4:30 PM ET)", "\n".join(report_lines))
    else:
        append_section("Post-Market Events (4:30 PM ET)", "No option events today.")

    logger.info("=== POST-MARKET JOB COMPLETE ===")
