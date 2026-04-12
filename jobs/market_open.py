"""Market open job — runs at 9:30 AM ET every weekday."""

import json
import logging
from dataclasses import asdict

from config import settings
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Evaluate wheel + spread strategies and execute trades.

    Uses StrategyRouter to decide which spread strategies are active.
    """
    logger.info("=== MARKET OPEN JOB STARTING ===")

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from ai.claude_advisor import ClaudeAdvisor
    from brokers.broker_factory import get_broker, make_broker
    from data.context_builder import ContextBuilder
    from data.spread_tracker import SpreadTracker
    from data.state_writer import StateWriter
    from data.trade_journal import TradeJournal
    from main import execute_decision
    from strategies.bear_call_spread_strategy import BearCallSpreadStrategy
    from strategies.bull_put_spread_strategy import BullPutSpreadStrategy
    from strategies.circuit_breaker import CircuitBreaker
    from strategies.guardrails import Guardrails
    from strategies.iron_condor_strategy import IronCondorStrategy
    from strategies.long_call_vertical_strategy import LongCallVerticalStrategy
    from strategies.strategy_router import StrategyRouter
    from strategies.wheel_strategy import WheelStrategy

    # Default broker for market-open check and circuit breaker
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

    try:
        sw.write_circuit_breaker_status(asdict(cb_status))
    except Exception as e:
        logger.warning("Failed to write circuit breaker snapshot: %s", e)

    cb.reset_daily()
    et_now = datetime.now(ZoneInfo(settings.TIMEZONE))
    if et_now.weekday() == 0:
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

    # ── Shared dependencies ─────────────────────────────────
    advisor = ClaudeAdvisor()
    guardrails = Guardrails()
    journal = TradeJournal(path=settings.JOURNAL_PATH)
    ctx_builder = ContextBuilder(broker=broker, journal=journal)
    tracker = SpreadTracker()
    router = StrategyRouter()

    # Each strategy gets a broker pointed at its designated account
    try:
        wheel_broker = make_broker("wheel")
    except ValueError:
        logger.warning("Wheel account credentials not set — using default")
        wheel_broker = broker
    wheel_strategy = WheelStrategy(wheel_broker)

    try:
        ic_broker = make_broker("iron_condor")
    except ValueError:
        logger.warning("Iron condor account credentials not set — using default")
        ic_broker = broker

    # Bull put, bear call, long call vertical share the default account
    default_broker = broker

    spread_strategies = {
        "iron_condor": IronCondorStrategy(broker=ic_broker, state_writer=sw, spread_tracker=tracker),
        "bull_put_spread": BullPutSpreadStrategy(broker=default_broker, state_writer=sw, spread_tracker=tracker),
        "bear_call_spread": BearCallSpreadStrategy(broker=default_broker, state_writer=sw, spread_tracker=tracker),
        "long_call_vertical": LongCallVerticalStrategy(broker=default_broker, state_writer=sw, spread_tracker=tracker),
    }

    # Portfolio snapshot
    try:
        positions = broker.get_positions()
        wheel_states = {}
        for sym in settings.WATCHLIST:
            try:
                wheel_states[sym] = wheel_strategy.get_current_state(sym).value
            except Exception:
                wheel_states[sym] = "UNKNOWN"
        strategy_states_snapshot = {
            "wheel": {sym: wheel_states.get(sym, "UNKNOWN") for sym in settings.WATCHLIST},
            **{name: s.get_state().value for name, s in spread_strategies.items()},
        }
        sw.write_portfolio_snapshot(
            account, positions, wheel_states,
            open_spreads=tracker.to_snapshot(),
        )
    except Exception as e:
        logger.warning("Failed to write portfolio snapshot: %s", e)

    report_lines: list[str] = []
    size_multiplier = cb.get_position_size_multiplier()

    if size_multiplier == 0.0:
        logger.warning("Circuit breaker: position size multiplier is 0 — no new positions")
        report_lines.append("**Circuit breaker RED** — no new positions allowed")

    # ── Wheel strategy (always runs, per symbol) ────────────
    for symbol in settings.WATCHLIST:
        try:
            state = wheel_strategy.get_current_state(symbol)
            logger.info("%s wheel state: %s", symbol, state.value)

            context = ctx_builder.build(symbol, state.value)
            logger.info(ContextBuilder.summarize_for_log(context))

            try:
                sw.write_context_snapshot(context)
            except Exception as e:
                logger.warning("Failed to write context snapshot for %s: %s", symbol, e)

            decision = advisor.ask(context, state)
            logger.info(
                "%s Claude decision: %s (confidence: %s)",
                symbol, decision.get("action"), decision.get("confidence"),
            )

            acct = context.get("account") or broker.get_account()
            pos = context.get("positions") or []
            is_valid, rejection = guardrails.validate(decision, acct, pos, context)

            if not is_valid:
                logger.warning("%s GUARDRAIL REJECTED: %s", symbol, rejection)
                try:
                    sw.write_decision(
                        decision_dict=decision, reasoning=decision.get("reasoning", ""),
                        action_taken=False, underlying=symbol, guardrail_rejection=rejection,
                    )
                except Exception as e:
                    logger.warning("Failed to write decision snapshot: %s", e)
                journal.append({
                    "symbol": decision.get("symbol"), "underlying": symbol,
                    "wheel_state": state.value, "action": "skip",
                    "reasoning": f"Guardrail rejected: {rejection}", "status": "skipped",
                })
                report_lines.append(f"**{symbol}** -- SKIPPED (guardrail: {rejection})")
                continue

            try:
                sw.write_decision(
                    decision_dict=decision, reasoning=decision.get("reasoning", ""),
                    action_taken=not settings.DRY_RUN, underlying=symbol,
                )
            except Exception as e:
                logger.warning("Failed to write decision snapshot: %s", e)

            if settings.DRY_RUN:
                logger.info("DRY RUN - would execute: %s", json.dumps(decision, default=str))
                report_lines.append(f"**{symbol}** -- DRY RUN: {decision.get('action')}")
                continue

            result = execute_decision(broker, decision)
            order_id = result.get("id") if result else None
            if result:
                logger.info("%s order executed: %s", symbol, order_id)
            else:
                logger.info("%s no order (action=%s)", symbol, decision.get("action"))

            journal.append({
                "symbol": decision.get("symbol"), "underlying": symbol,
                "wheel_state": state.value, "action": decision.get("action"),
                "contract_symbol": decision.get("symbol"), "qty": decision.get("qty"),
                "limit_price": decision.get("limit_price"),
                "confidence": decision.get("confidence"),
                "reasoning": decision.get("reasoning"), "order_id": order_id,
                "status": "submitted" if result else decision.get("action"),
                "fill_status": "pending" if result else None,
            })
            report_lines.append(
                f"**{symbol}** -- {decision.get('action')} "
                f"(confidence: {decision.get('confidence')})"
            )

        except Exception:
            logger.exception("Market open failed for %s — continuing", symbol)
            report_lines.append(f"**{symbol}** -- ERROR (see logs)")

    wheel_strategy.save_state()

    # ── Spread strategies (router-driven) ───────────────────
    # Build context once for routing (use first watchlist symbol)
    shared_context = None
    if settings.WATCHLIST:
        try:
            shared_context = ctx_builder.build(settings.WATCHLIST[0], "IDLE")
        except Exception:
            logger.exception("Failed to build shared context for spread routing")

    if shared_context:
        strat_states = {name: s.get_state().value for name, s in spread_strategies.items()}
        active = router.get_active_strategies(
            shared_context, strat_states, circuit_breaker_status=cb_status.status,
        )
        active_spreads = [a for a in active if a != "wheel"]

        logger.info(
            "Strategy router: active=%s (states: %s)",
            active, strat_states,
        )

        for strategy_name in active_spreads:
            strat = spread_strategies[strategy_name]
            try:
                # TODO: each spread strategy should eventually specify its
                # own preferred underlying rather than reusing WATCHLIST[0].
                try:
                    spread_ctx = ctx_builder.build(settings.WATCHLIST[0], "IDLE")
                except Exception:
                    logger.exception(
                        "Failed to build context for spread strategy %s", strategy_name,
                    )
                    report_lines.append(f"**{strategy_name}** -- ERROR (context build)")
                    continue

                decision = strat.run_cycle(spread_ctx, advisor)
                action = decision.get("action", "SKIP")
                logger.info("%s decision: %s", strategy_name, action)

                try:
                    sw.write_decision(
                        decision_dict=decision,
                        reasoning=decision.get("reasoning", ""),
                        action_taken=action in ("OPEN", "CLOSE"),
                        underlying=strategy_name,
                    )
                except Exception as e:
                    logger.warning("Failed to write %s decision: %s", strategy_name, e)

                if action == "OPEN":
                    _handle_spread_open(
                        strategy_name, strat, decision, guardrails,
                        spread_ctx, account, tracker, settings, report_lines,
                    )
                elif action == "CLOSE" and decision.get("spread_id"):
                    _handle_spread_close(
                        strategy_name, strat, decision, settings, report_lines,
                    )
                else:
                    report_lines.append(f"**{strategy_name}** -- {action}")

            except Exception:
                logger.exception("%s strategy failed — continuing", strategy_name)
                report_lines.append(f"**{strategy_name}** -- ERROR (see logs)")

    append_section("Market Open Decisions (9:30 AM ET)", "\n".join(report_lines))
    logger.info("=== MARKET OPEN JOB COMPLETE ===")


# ── Spread helpers ──────────────────────────────────────────

_GUARDRAIL_MAP = {
    "iron_condor": "validate_iron_condor_entry",
    "bull_put_spread": "validate_bull_put_spread_entry",
    "bear_call_spread": "validate_bear_call_spread_entry",
    "long_call_vertical": "validate_long_call_vertical_entry",
}


def _handle_spread_open(
    name, strat, decision, guardrails, context, account, tracker, settings, report_lines,
):
    """Validate and execute a spread OPEN decision."""
    validator_name = _GUARDRAIL_MAP.get(name)
    if not validator_name:
        logger.error(
            "No guardrail mapped for strategy '%s' — skipping trade. "
            "Add it to _GUARDRAIL_MAP before deploying.", name,
        )
        report_lines.append(f"**{name}** -- SKIPPED (no guardrail mapped)")
        return

    validator = getattr(guardrails, validator_name)
    open_spreads = tracker.get_open_spreads(strategy_type=name)
    if name == "iron_condor":
        is_valid, rejection = validator(decision, context, account, open_condors=open_spreads)
    else:
        is_valid, rejection = validator(decision, context, account, open_spreads=open_spreads)

    if not is_valid:
        logger.warning("%s GUARDRAIL REJECTED: %s", name, rejection)
        report_lines.append(f"**{name}** -- REJECTED: {rejection}")
        return

    underlying = decision.get("underlying", settings.WATCHLIST[0] if settings.WATCHLIST else "")

    if settings.DRY_RUN:
        logger.info("DRY RUN - would open %s: %s", name, json.dumps(decision, default=str))
        report_lines.append(f"**{name}** -- DRY RUN: OPEN")
        return

    success = strat.execute_entry({**decision, "underlying": underlying})
    if success:
        report_lines.append(
            f"**{name}** -- OPENED (credit/debit: "
            f"${decision.get('total_credit') or decision.get('net_credit') or decision.get('net_debit', 0)})"
        )
    else:
        report_lines.append(f"**{name}** -- OPEN FAILED")


def _handle_spread_close(name, strat, decision, settings, report_lines):
    """Execute a spread CLOSE decision."""
    if settings.DRY_RUN:
        logger.info("DRY RUN - would close %s", name)
        report_lines.append(f"**{name}** -- DRY RUN: CLOSE")
        return

    success = strat.execute_exit(
        decision["spread_id"], limit_price=decision.get("limit_price"),
    )
    report_lines.append(f"**{name}** -- {'CLOSED' if success else 'CLOSE FAILED'}")
