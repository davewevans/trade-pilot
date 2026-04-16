"""Position check job — runs at 10:00 AM, 12:00 PM, and 2:00 PM ET."""

import json
import logging
from dataclasses import asdict
from datetime import datetime

from config import settings
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Check open options positions and ask Claude whether to roll, close, or hold."""
    logger.info("=== POSITION CHECK JOB STARTING ===")

    from ai.claude_advisor import ClaudeAdvisor
    from brokers.broker_factory import get_broker, make_broker
    from data.context_builder import ContextBuilder
    from data.state_writer import StateWriter
    from data.trade_journal import TradeJournal
    from main import execute_decision
    from strategies.circuit_breaker import CircuitBreaker
    from strategies.guardrails import Guardrails
    from strategies.wheel_strategy import WheelStrategy

    # Wheel positions live in the wheel-specific account.
    try:
        broker = make_broker("wheel")
    except ValueError:
        broker = get_broker()  # fallback to default if wheel creds not configured
    sw = StateWriter()

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

    # Write circuit breaker + portfolio snapshots
    try:
        sw.write_circuit_breaker_status(asdict(cb_status))
    except Exception as e:
        logger.warning("Failed to write circuit breaker snapshot: %s", e)

    try:
        positions_all = broker.get_positions()
        sw.write_portfolio_snapshot(
            account, positions_all, {},
            wheel_symbols=list(settings.WATCHLIST),
        )
    except Exception as e:
        logger.warning("Failed to write portfolio snapshot: %s", e)

    # ── Open positions ──────────────────────────────────────
    positions = broker.get_positions()
    if not positions:
        logger.info("No open positions to check.")
        return

    guardrails = Guardrails(broker=broker)
    strategy = WheelStrategy(broker)
    journal = TradeJournal(path=settings.JOURNAL_PATH)
    ctx_builder = ContextBuilder(broker=broker, journal=journal)

    # Dual-write recorder for SQLite (failures logged, never block job).
    from database.db import Database
    from database.recorder import TradeRecorder
    from database.repositories import ApiUsageRepository
    try:
        _db = Database()
        _db.init_schema()
        _conn = _db.get_connection()
        recorder = TradeRecorder(_conn)
        api_usage_repo = ApiUsageRepository(_conn)
    except Exception:
        logger.exception("Failed to initialize DB recorder — continuing without DB writes")
        recorder = None
        api_usage_repo = None

    advisor = ClaudeAdvisor(api_usage_repo=api_usage_repo)

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

            # Write context snapshot
            try:
                sw.write_context_snapshot(context)
            except Exception as e:
                logger.warning("Failed to write context snapshot for %s: %s", underlying, e)

            import time as _time
            _t0_ask = _time.monotonic()
            decision = advisor.ask(context, state)
            _elapsed_ask_ms = int((_time.monotonic() - _t0_ask) * 1000)
            if recorder is not None:
                recorder.record_token_usage(
                    strategy_type="wheel",
                    underlying=underlying,
                    model=advisor.model,
                    usage=advisor.prompt_cache_stats or {},
                    response_time_ms=_elapsed_ask_ms,
                    decision_action=decision.get("action"),
                )
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

                    try:
                        sw.write_decision(
                            decision_dict=decision,
                            reasoning=decision.get("reasoning", ""),
                            action_taken=False,
                            underlying=underlying,
                            guardrail_rejection=rejection,
                        )
                    except Exception as e:
                        logger.warning("Failed to write decision snapshot for %s: %s", underlying, e)

                    if recorder is not None:
                        recorder.record_decision(
                            strategy_type="wheel", underlying=underlying,
                            action="SKIP",
                            wheel_state=state.value,
                            reasoning=f"Guardrail rejected: {rejection}",
                            confidence=decision.get("confidence"),
                            context=context,
                        )

                    report_lines.append(f"**{underlying}** — {action} REJECTED: {rejection}")
                    continue

                # Write accepted decision snapshot
                try:
                    sw.write_decision(
                        decision_dict=decision,
                        reasoning=decision.get("reasoning", ""),
                        action_taken=not settings.DRY_RUN,
                        underlying=underlying,
                    )
                except Exception as e:
                    logger.warning("Failed to write decision snapshot for %s: %s", underlying, e)

                decision_id_db = None
                cycle_id_db = None
                if recorder is not None:
                    decision_id_db, cycle_id_db = recorder.record_decision(
                        strategy_type="wheel", underlying=underlying,
                        action=action,
                        wheel_state=state.value,
                        reasoning=decision.get("reasoning"),
                        confidence=decision.get("confidence"),
                        context=context,
                    )

                if settings.DRY_RUN:
                    logger.info("DRY RUN - would execute: %s", json.dumps(decision, default=str))
                    report_lines.append(f"**{underlying}** — DRY RUN: {action}")
                    continue

                # For rolls, provide the existing position's OCC so
                # execute_decision can buy-to-close it before opening the new one.
                if action == "roll":
                    decision["existing_symbol"] = pos.get("symbol") or symbol_occ
                elif action == "close" and not decision.get("symbol"):
                    decision["symbol"] = pos.get("symbol") or symbol_occ

                result = execute_decision(broker, decision)
                order_id = result.get("id") if result else None
                if order_id:
                    journal.update(pos.get("order_id", ""), {
                        "status": "rolled" if action == "roll" else "closed",
                        "fill_status": "pending",
                        "closed_at": datetime.now().isoformat(timespec="seconds"),
                        "close_iv_rank": context.get("iv_rank"),
                        "close_vix": (context.get("macro") or {}).get("vix"),
                        "close_regime": context.get("confirmed_market_regime"),
                        "close_delta": decision.get("delta"),
                        "close_dte": decision.get("dte"),
                    })
                    logger.info("%s %s executed: %s", underlying, action, order_id)

                    if recorder is not None and cycle_id_db:
                        recorder.record_trade(
                            cycle_id=cycle_id_db,
                            decision_id=decision_id_db,
                            alpaca_order_id=order_id,
                            underlying=underlying,
                            strategy_type="wheel",
                            action=action,
                            symbol=decision.get("symbol", ""),
                            limit_price=decision.get("limit_price") or 0.0,
                            contracts=int(decision.get("qty") or 1),
                        )
                report_lines.append(f"**{underlying}** — {action} executed")
            else:
                # Write hold decision snapshot
                try:
                    sw.write_decision(
                        decision_dict=decision,
                        reasoning=decision.get("reasoning", ""),
                        action_taken=False,
                        underlying=underlying,
                    )
                except Exception as e:
                    logger.warning("Failed to write decision snapshot for %s: %s", underlying, e)

                if recorder is not None:
                    recorder.record_decision(
                        strategy_type="wheel", underlying=underlying,
                        action=action or "HOLD",
                        wheel_state=state.value,
                        reasoning=decision.get("reasoning"),
                        confidence=decision.get("confidence"),
                        context=context,
                    )

                report_lines.append(f"**{underlying}** — hold")

        except Exception:
            logger.exception("Position check failed for %s — continuing", underlying)
            report_lines.append(f"**{underlying}** — ERROR (see logs)")

    strategy.save_state()

    if report_lines:
        append_section(f"Position Check ({now_str})", "\n".join(report_lines))

    logger.info("=== POSITION CHECK JOB COMPLETE ===")
