"""Market open job — runs at 9:30 AM ET every weekday."""

import json
import logging

from config import settings
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Evaluate each watchlist symbol and execute trades via the wheel strategy.

    Checks market status first; returns early on holidays.
    """
    logger.info("=== MARKET OPEN JOB STARTING ===")

    from ai.claude_advisor import ClaudeAdvisor
    from brokers.broker_factory import get_broker
    from data.context_builder import ContextBuilder
    from data.trade_journal import TradeJournal
    from main import execute_decision
    from strategies.guardrails import Guardrails
    from strategies.wheel_strategy import WheelStrategy

    broker = get_broker()

    # ── Market-open check ───────────────────────────────────
    clock = broker.get_clock()
    if not clock.get("is_open"):
        logger.info("Market is closed (holiday/weekend). Exiting early.")
        return

    advisor = ClaudeAdvisor()
    guardrails = Guardrails()
    strategy = WheelStrategy(broker)
    journal = TradeJournal(path=settings.JOURNAL_PATH)
    ctx_builder = ContextBuilder(broker=broker, journal=journal)

    report_lines: list[str] = []

    for symbol in settings.WATCHLIST:
        try:
            # Determine wheel state
            state = strategy.get_current_state(symbol)
            logger.info("%s wheel state: %s", symbol, state.value)

            # Build full context
            context = ctx_builder.build(symbol, state.value)
            logger.info(ContextBuilder.summarize_for_log(context))

            # Ask Claude
            decision = advisor.ask(context, state)
            logger.info(
                "%s Claude decision: %s (confidence: %s)",
                symbol,
                decision.get("action"),
                decision.get("confidence"),
            )

            # Validate via guardrails
            account = context.get("account") or broker.get_account()
            positions = context.get("positions") or []
            is_valid, rejection = guardrails.validate(decision, account, positions, context)

            if not is_valid:
                logger.warning("%s GUARDRAIL REJECTED: %s", symbol, rejection)
                journal.append({
                    "symbol": decision.get("symbol"),
                    "underlying": symbol,
                    "wheel_state": state.value,
                    "action": "skip",
                    "reasoning": f"Guardrail rejected: {rejection}",
                    "status": "skipped",
                })
                report_lines.append(f"**{symbol}** — SKIPPED (guardrail: {rejection})")
                continue

            if settings.DRY_RUN:
                logger.info("DRY RUN - would execute: %s", json.dumps(decision, default=str))
                report_lines.append(f"**{symbol}** — DRY RUN: {decision.get('action')}")
                continue

            # Execute
            result = execute_decision(broker, decision)
            order_id = result.get("id") if result else None
            if result:
                logger.info("%s order executed: %s", symbol, order_id)
            else:
                logger.info("%s no order (action=%s)", symbol, decision.get("action"))

            # Journal
            journal.append({
                "symbol": decision.get("symbol"),
                "underlying": symbol,
                "wheel_state": state.value,
                "action": decision.get("action"),
                "contract_symbol": decision.get("symbol"),
                "qty": decision.get("qty"),
                "limit_price": decision.get("limit_price"),
                "confidence": decision.get("confidence"),
                "reasoning": decision.get("reasoning"),
                "order_id": order_id,
                "status": "submitted" if result else decision.get("action"),
            })

            report_lines.append(
                f"**{symbol}** — {decision.get('action')} "
                f"(confidence: {decision.get('confidence')})"
            )

        except Exception:
            logger.exception("Market open failed for %s — continuing", symbol)
            report_lines.append(f"**{symbol}** — ERROR (see logs)")

    # Save strategy state after all symbols processed
    strategy.save_state()

    append_section("Market Open Decisions (9:30 AM ET)", "\n".join(report_lines))
    logger.info("=== MARKET OPEN JOB COMPLETE ===")
