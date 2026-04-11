"""Position check job — runs at 10:00 AM, 12:00 PM, and 2:00 PM ET."""

import json
import logging
from datetime import datetime

from config import settings
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Check open options positions and ask Claude whether to roll, close, or hold."""
    logger.info("=== POSITION CHECK JOB STARTING ===")

    from ai.claude_advisor import ClaudeAdvisor
    from brokers.broker_factory import get_broker
    from data.context_builder import ContextBuilder
    from data.trade_journal import TradeJournal
    from main import execute_decision
    from strategies.circuit_breaker import CircuitBreaker
    from strategies.guardrails import Guardrails
    from strategies.wheel_strategy import WheelStrategy

    broker = get_broker()

    # ── Market-open check ───────────────────────────────────
    clock = broker.get_clock()
    if not clock.get("is_open"):
        logger.info("Market is closed. Exiting early.")
        return

    # ── Circuit breaker check ───────────────────────────────
    cb = CircuitBreaker()
    if cb.is_halted():
        logger.warning("CIRCUIT BREAKER HALTED — skipping position check")
        return

    account = broker.get_account()
    equity = float(account.get("portfolio_value", 0))
    cb_status = cb.update(equity)
    logger.info(
        "Circuit breaker: %s | Daily P&L: %.2f%% | Drawdown: %.2f%%",
        cb_status.status, cb_status.daily_pnl_pct, cb_status.drawdown_pct,
    )

    # ── Open positions ──────────────────────────────────────
    positions = broker.get_positions()
    if not positions:
        logger.info("No open positions to check.")
        return

    advisor = ClaudeAdvisor()
    guardrails = Guardrails()
    strategy = WheelStrategy(broker)
    journal = TradeJournal(path=settings.JOURNAL_PATH)
    ctx_builder = ContextBuilder(broker=broker, journal=journal)

    now_str = datetime.now().strftime("%I:%M %p ET")
    report_lines: list[str] = []

    for pos in positions:
        symbol_occ = pos.get("symbol", "")
        # Extract the underlying root ticker (first 1-6 non-digit chars)
        underlying = ""
        for ch in symbol_occ:
            if ch.isalpha():
                underlying += ch
            else:
                break
        underlying = underlying.upper() or symbol_occ

        try:
            state = strategy.get_current_state(underlying)
            context = ctx_builder.build(underlying, state.value)
            logger.info(ContextBuilder.summarize_for_log(context))

            decision = advisor.ask(context, state)
            action = decision.get("action", "hold")
            logger.info(
                "%s position check: %s (confidence: %s)",
                underlying, action, decision.get("confidence"),
            )

            if action in ("roll", "close"):
                account = context.get("account") or broker.get_account()
                is_valid, rejection = guardrails.validate(
                    decision, account, positions, context,
                )
                if not is_valid:
                    logger.warning("%s GUARDRAIL REJECTED: %s", underlying, rejection)
                    report_lines.append(f"**{underlying}** — {action} REJECTED: {rejection}")
                    continue

                if settings.DRY_RUN:
                    logger.info("DRY RUN - would execute: %s", json.dumps(decision, default=str))
                    report_lines.append(f"**{underlying}** — DRY RUN: {action}")
                    continue

                result = execute_decision(broker, decision)
                order_id = result.get("id") if result else None
                if order_id:
                    journal.update(pos.get("order_id", ""), {
                        "status": "rolled" if action == "roll" else "closed",
                        "closed_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    logger.info("%s %s executed: %s", underlying, action, order_id)
                report_lines.append(f"**{underlying}** — {action} executed")
            else:
                report_lines.append(f"**{underlying}** — hold")

        except Exception:
            logger.exception("Position check failed for %s — continuing", underlying)
            report_lines.append(f"**{underlying}** — ERROR (see logs)")

    strategy.save_state()

    if report_lines:
        append_section(f"Position Check ({now_str})", "\n".join(report_lines))

    logger.info("=== POSITION CHECK JOB COMPLETE ===")
