"""Market open job — runs at 9:30 AM ET every weekday."""

import json
import logging
from dataclasses import asdict

from config import settings
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Evaluate each watchlist symbol and execute trades via the wheel strategy.

    Checks market status first; returns early on holidays.
    """
    logger.info("=== MARKET OPEN JOB STARTING ===")

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ai.claude_advisor import ClaudeAdvisor
    from brokers.broker_factory import get_broker
    from data.context_builder import ContextBuilder
    from data.state_writer import StateWriter
    from data.trade_journal import TradeJournal
    from main import execute_decision
    from strategies.circuit_breaker import CircuitBreaker
    from strategies.guardrails import Guardrails
    from strategies.wheel_strategy import WheelStrategy

    broker = get_broker()
    sw = StateWriter()

    # ── Market-open check ───────────────────────────────────
    clock = broker.get_clock()
    if not clock.get("is_open"):
        logger.info("Market is closed (holiday/weekend). Exiting early.")
        return

    # ── Circuit breaker ─────────────────────────────────────
    cb = CircuitBreaker()
    account = broker.get_account()
    equity = float(account.get("portfolio_value", 0))
    cb_status = cb.update(equity)

    # Write circuit breaker snapshot
    try:
        sw.write_circuit_breaker_status(asdict(cb_status))
    except Exception as e:
        logger.warning("Failed to write circuit breaker snapshot: %s", e)

    # Daily reset (always on market open)
    cb.reset_daily()

    # Weekly reset on Monday
    et_now = datetime.now(ZoneInfo(settings.TIMEZONE))
    if et_now.weekday() == 0:  # Monday
        cb.reset_weekly()

    if cb.is_halted():
        logger.warning("CIRCUIT BREAKER HALTED — skipping all trading decisions")
        append_section(
            "Market Open Decisions (9:30 AM ET)",
            "**HALTED** — Circuit breaker lock file active. No trades placed.",
        )
        logger.info("=== MARKET OPEN JOB COMPLETE (halted) ===")
        return

    logger.info(
        "Circuit breaker: %s | Daily P&L: %.2f%% | Drawdown: %.2f%% | Multiplier: %.1f",
        cb_status.status, cb_status.daily_pnl_pct, cb_status.drawdown_pct,
        cb.get_position_size_multiplier(),
    )

    advisor = ClaudeAdvisor()
    guardrails = Guardrails()
    strategy = WheelStrategy(broker)
    journal = TradeJournal(path=settings.JOURNAL_PATH)
    ctx_builder = ContextBuilder(broker=broker, journal=journal)
    size_multiplier = cb.get_position_size_multiplier()

    # Write portfolio snapshot before decisions
    try:
        positions = broker.get_positions()
        wheel_states = {}
        for sym in settings.WATCHLIST:
            try:
                wheel_states[sym] = strategy.get_current_state(sym).value
            except Exception:
                wheel_states[sym] = "UNKNOWN"
        sw.write_portfolio_snapshot(account, positions, wheel_states)
    except Exception as e:
        logger.warning("Failed to write portfolio snapshot: %s", e)

    report_lines: list[str] = []

    if size_multiplier == 0.0:
        logger.warning("Circuit breaker: position size multiplier is 0 — no new positions")
        report_lines.append("**Circuit breaker RED** — no new positions allowed")

    for symbol in settings.WATCHLIST:
        try:
            # Determine wheel state
            state = strategy.get_current_state(symbol)
            logger.info("%s wheel state: %s", symbol, state.value)

            # Build full context
            context = ctx_builder.build(symbol, state.value)
            logger.info(ContextBuilder.summarize_for_log(context))

            # Write context snapshot
            try:
                sw.write_context_snapshot(context)
            except Exception as e:
                logger.warning("Failed to write context snapshot for %s: %s", symbol, e)

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

                # Write rejected decision snapshot
                try:
                    sw.write_decision(
                        decision_dict=decision,
                        reasoning=decision.get("reasoning", ""),
                        action_taken=False,
                        underlying=symbol,
                        guardrail_rejection=rejection,
                    )
                except Exception as e:
                    logger.warning("Failed to write decision snapshot for %s: %s", symbol, e)

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

            # Write accepted decision snapshot
            try:
                sw.write_decision(
                    decision_dict=decision,
                    reasoning=decision.get("reasoning", ""),
                    action_taken=not settings.DRY_RUN,
                    underlying=symbol,
                )
            except Exception as e:
                logger.warning("Failed to write decision snapshot for %s: %s", symbol, e)

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
