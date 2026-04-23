"""trade-pilot — unified entry point.

Usage:
  python main.py                                  # start scheduler
  python main.py --job pre_market                 # run pre-market job now
  python main.py --job market_open --dry-run      # test without trading
  python main.py --job market_open --symbol AAPL  # one symbol only
"""

import argparse
import importlib
import json
import logging
import logging.handlers
import os
import sys
import time

logger = logging.getLogger("trade-pilot")

JOB_MODULES = {
    "pre_market": "jobs.pre_market",
    "market_open": "jobs.market_open",
    "position_check": "jobs.position_check",
    "expiry_guard": "jobs.expiry_guard",
    "pre_close": "jobs.pre_close",
    "market_close": "jobs.market_close",
    "post_market": "jobs.post_market",
    "weekly_report": "jobs.weekly_report",
    "weekly_research": "jobs.weekly_research",
}

# Jobs handled inline (not via a module's run()):
_INLINE_JOBS = {"score_programmatic", "score_judge", "monthly_evaluation"}


# ── CLI ────────────────────────────────��────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trade-pilot wheel strategy bot",
        epilog=(
            "examples:\n"
            "  python main.py                                  # start scheduler\n"
            "  python main.py --job pre_market                 # run one job\n"
            "  python main.py --job market_open --dry-run      # test without trading\n"
            "  python main.py --job market_open --symbol AAPL  # single symbol\n"
            "  python main.py --job score_programmatic --start-date 2026-04-01 --end-date 2026-04-30\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--job",
        choices=sorted(list(JOB_MODULES.keys()) + list(_INLINE_JOBS)),
        help="Run a single job by name, then exit",
    )
    parser.add_argument(
        "--start-date",
        help="Start date for score_programmatic job (YYYY-MM-DD, defaults to 30 days ago)",
    )
    parser.add_argument(
        "--end-date",
        help="End date for score_programmatic job (YYYY-MM-DD, defaults to today)",
    )
    parser.add_argument(
        "--month",
        help=(
            "Target month for monthly_evaluation job (YYYY-MM). "
            "Omit to use the prior calendar month. "
            "Use this to backfill or regenerate a specific month."
        ),
    )
    parser.add_argument(
        "--symbol",
        help="Override WATCHLIST with a single symbol (e.g. AAPL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Override DRY_RUN to True — log decisions but do not trade",
    )
    parser.add_argument(
        "--force-restart-sweep",
        action="store_true",
        default=False,
        help=(
            "Discard stale in-progress sweep state and start fresh "
            "(sets FORCE_RESTART_SWEEP=1)"
        ),
    )
    return parser.parse_args()


# ── order execution helper (used by jobs) ───────────────────


def execute_decision(broker, decision: dict) -> dict | None:
    """Translate a Claude recommendation into a broker order.

    Args:
        broker: A BaseBroker instance.
        decision: Validated recommendation dict.

    Returns:
        The order result dict (possibly augmented with fill_price /
        fill_status after the inline 30-second confirmation window),
        or None for hold/skip/aborted rolls.
    """
    from config import settings

    action = decision["action"]

    if action in ("hold", "skip"):
        return None

    result: dict | None = None

    def _confirm_fill(order_result: dict | None, *, limit_price_used=None) -> str | None:
        """Wait 30 s then check order fill status.

        Skipped entirely when DRY_RUN is True or when *order_result*
        has no order id.  Updates *order_result* in-place with
        ``fill_price`` and ``fill_status`` keys so callers can log
        accurate data.

        Returns the broker status string (lowercase) or None if the
        check was skipped or failed.
        """
        if settings.DRY_RUN:
            return None
        order_id = (order_result or {}).get("id")
        if not order_id:
            return None

        time.sleep(30)
        try:
            order_status = broker.get_order(order_id)
            fill_price = order_status.get("filled_avg_price")
            status = str(order_status.get("status", "")).lower()

            if status == "filled":
                logger.info(
                    "Order %s FILLED at %s (requested %s)",
                    order_id, fill_price, limit_price_used,
                )
                # Fix 5: log fill-vs-limit slippage
                if fill_price is not None and limit_price_used is not None:
                    try:
                        slippage = float(fill_price) - float(limit_price_used)
                        logger.info(
                            "Fill slippage: %+.2f (fill=%.2f, limit=%.2f) on %s",
                            slippage, float(fill_price), float(limit_price_used),
                            decision.get("symbol", ""),
                        )
                    except (TypeError, ValueError):
                        pass
                try:
                    if settings.ALERT_FILLS:
                        from notifications.fill_buffer import record as _record_fill
                        _record_fill(
                            action=decision.get("action", ""),
                            symbol=decision.get("symbol", ""),
                            fill_price=fill_price,
                            limit_price=limit_price_used,
                        )
                except Exception:
                    pass
            elif status in ("canceled", "cancelled", "rejected"):
                logger.warning(
                    "Order %s was %s — trade did NOT execute. Reason: %s",
                    order_id, status,
                    order_status.get("reject_reason", "unknown"),
                )
                try:
                    from notifications import notify as _notify
                    _notify(
                        "high",
                        f"Order rejected: {decision.get('action', '')} {decision.get('symbol', '')}",
                        f"Status: {status}. Reason: {order_status.get('reject_reason', 'unknown')}",
                        tags=["rejection"],
                    )
                except Exception:
                    pass
            elif status in ("new", "accepted", "pending_new", "partially_filled"):
                logger.info(
                    "Order %s still %s after 30s — "
                    "will be resolved by market_close reconciler",
                    order_id, status,
                )

            # Attach fill data so callers (market_open, position_check) can
            # record accurate fill prices without waiting for market_close.
            if order_result is not None:
                order_result["fill_price"] = fill_price
                order_result["fill_status"] = status
            return status
        except Exception:
            logger.warning(
                "Could not confirm fill for order %s — "
                "will be resolved by market_close reconciler",
                order_id, exc_info=True,
            )
            return None

    _strategy_type = decision.get("strategy_type", "")

    if action in ("sell_put", "sell_call"):
        result = broker.place_order(
            symbol=decision["symbol"],
            qty=decision["qty"],
            side="sell",
            order_type=decision["order_type"],
            time_in_force="day",
            limit_price=decision.get("limit_price"),
        )
        _confirm_fill(result, limit_price_used=decision.get("limit_price"))
        try:
            from data.shadow_execution import record_submission as _shadow_record
            _shadow_record(
                strategy_type=_strategy_type,
                action=action,
                legs=[{
                    "contract_symbol": decision["symbol"],
                    "leg_role": "single",
                    "side": "sell",
                    "position_intent": "sell_to_open",
                }],
                net_limit_price=-abs(decision.get("limit_price") or 0),
                alpaca_order_id=(result or {}).get("id"),
            )
        except Exception:
            logger.debug("shadow_execution recording failed (non-fatal)", exc_info=True)

    elif action == "close":
        # Buy-to-close the existing short option position.
        result = broker.place_order(
            symbol=decision["symbol"],
            qty=decision["qty"],
            side="buy",
            order_type=decision.get("order_type", "limit"),
            time_in_force="day",
            limit_price=decision.get("limit_price"),
        )
        _confirm_fill(result, limit_price_used=decision.get("limit_price"))
        try:
            from data.shadow_execution import record_submission as _shadow_record
            _shadow_record(
                strategy_type=_strategy_type,
                action="close_short",
                legs=[{
                    "contract_symbol": decision["symbol"],
                    "leg_role": "single",
                    "side": "buy",
                    "position_intent": "buy_to_close",
                }],
                net_limit_price=+abs(decision.get("limit_price") or 0),
                alpaca_order_id=(result or {}).get("id"),
            )
        except Exception:
            logger.debug("shadow_execution recording failed (non-fatal)", exc_info=True)

    elif action == "roll":
        # Roll = buy-to-close existing short option, then sell-to-open replacement.
        existing_symbol = decision.get("existing_symbol")
        if not existing_symbol:
            logger.error(
                "Roll action missing 'existing_symbol' — aborting to avoid "
                "opening a second short without closing the first"
            )
            return None

        close_limit = decision.get("close_limit_price") or decision.get("limit_price")
        close_result = broker.place_order(
            symbol=existing_symbol,
            qty=decision["qty"],
            side="buy",
            order_type="limit",
            time_in_force="day",
            limit_price=close_limit,
        )
        close_id = (close_result or {}).get("id")
        if not close_id:
            logger.error(
                "Roll: buy-to-close failed for %s — NOT placing replacement sell order",
                existing_symbol,
            )
            return None
        logger.info("Roll: buy-to-close %s submitted (order %s)", existing_symbol, close_id)

        close_status = _confirm_fill(close_result, limit_price_used=close_limit)

        # Record the close leg before checking for abort
        try:
            from data.shadow_execution import record_submission as _shadow_record
            _shadow_record(
                strategy_type=_strategy_type,
                action="roll_close",
                legs=[{
                    "contract_symbol": existing_symbol,
                    "leg_role": "single",
                    "side": "buy",
                    "position_intent": "buy_to_close",
                }],
                net_limit_price=+abs(close_limit or 0),
                alpaca_order_id=close_id,
            )
        except Exception:
            logger.debug("shadow_execution recording failed (non-fatal)", exc_info=True)

        if close_status in ("canceled", "cancelled", "rejected"):
            logger.warning(
                "Roll: close leg %s was %s — NOT placing replacement sell order",
                close_id, close_status,
            )
            return None

        open_result = broker.place_order(
            symbol=decision["symbol"],
            qty=decision["qty"],
            side="sell",
            order_type=decision["order_type"],
            time_in_force="day",
            limit_price=decision.get("limit_price"),
        )
        open_id = (open_result or {}).get("id")
        logger.info("Roll: sell-to-open %s submitted (order %s)", decision["symbol"], open_id)
        _confirm_fill(open_result, limit_price_used=decision.get("limit_price"))

        try:
            from data.shadow_execution import record_submission as _shadow_record
            _shadow_record(
                strategy_type=_strategy_type,
                action="roll_open",
                legs=[{
                    "contract_symbol": decision["symbol"],
                    "leg_role": "single",
                    "side": "sell",
                    "position_intent": "sell_to_open",
                }],
                net_limit_price=-abs(decision.get("limit_price") or 0),
                alpaca_order_id=(open_result or {}).get("id"),
            )
        except Exception:
            logger.debug("shadow_execution recording failed (non-fatal)", exc_info=True)

        result = open_result

    else:
        logger.warning("Unhandled action: %s", action)
        return None

    return result


# ── startup validation ──────────────────────────────────────


def validate_startup() -> None:
    """Verify env vars, broker connectivity, and account health.

    Exits the process if any critical check fails.
    """
    from config import settings

    # 1. Required env vars (Settings._require already enforces these
    #    at import time, but be explicit for clarity)
    for var in ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY", "ANTHROPIC_API_KEY"):
        if not getattr(settings, var, None):
            logger.error("Missing required environment variable: %s", var)
            sys.exit(1)

    # 2. Test Alpaca connection
    from brokers.broker_factory import get_broker

    try:
        broker = get_broker()
        account = broker.get_account()
    except Exception:
        logger.exception("Failed to connect to broker — check API keys")
        sys.exit(1)

    # 3. Log account status
    logger.info(
        "Account: status=%s | buying_power=%s | portfolio_value=%s | options_level=%s",
        account.get("status"),
        account.get("buying_power"),
        account.get("portfolio_value"),
        account.get("options_trading_level"),
    )

    # 4. Options trading level check
    if account.get("options_trading_level") == 0:
        logger.error(
            "Options trading is not enabled on this account (level=0). Exiting."
        )
        sys.exit(1)

    # 5. Initialize SQLite schema (idempotent — safe on every startup)
    try:
        from database.db import Database

        db = Database()
        db.init_schema()
        db.close()
    except Exception:
        logger.exception("Failed to initialize SQLite schema — exiting")
        sys.exit(1)

    logger.info("Startup validation passed")

    # Resolve any pending trades from prior sessions (crashes, Render restarts,
    # market_close failures) before any new trading decisions are made.
    try:
        from database.db import Database
        from database.recorder import TradeRecorder
        from jobs.reconcile_orders import reconcile_all_pending

        _db = Database()
        _db.init_schema()
        _recorder = TradeRecorder(_db.get_connection())
        reconcile_all_pending(broker, _recorder)
    except Exception as e:
        logger.warning("Startup reconciler failed (non-fatal): %s", e)

    # Write initial portfolio snapshots for all accounts so the
    # dashboard has data before the first scheduled cycle runs.
    try:
        from jobs.startup_snapshot import run as run_startup_snapshot
        run_startup_snapshot()
    except Exception as e:
        logger.warning("Startup snapshot failed (non-fatal): %s", e)

    # Reconcile pending order state across all accounts (spread tracker +
    # SQLite trades) so the bot starts with accurate position state.
    reconciler_summary: dict = {}
    try:
        from jobs.startup_reconciler import run as run_startup_reconciler
        reconciler_summary = run_startup_reconciler()
        logger.info("Startup reconciler: %s", reconciler_summary)
    except Exception:
        logger.exception("Startup reconciler failed — continuing without reconciliation")

    if reconciler_summary.get("halted"):
        logger.critical(
            "Startup reconciler wrote HALTED.lock — aborting bot startup. "
            "Investigate partial fills and delete HALTED.lock manually to resume."
        )
        sys.exit(1)

    # Broker-truth state reconciliation — runs AFTER pending-order reconciler,
    # BEFORE liveness notification. Compares SQLite/JSON local state against
    # live Alpaca positions. In enforce mode, halts on large mismatches.
    broker_reconcile_summary: dict = {}
    try:
        from jobs.startup_broker_reconcile import run as run_broker_reconcile
        broker_reconcile_summary = run_broker_reconcile()
        logger.info("Startup broker reconcile: %s", broker_reconcile_summary)
    except Exception:
        logger.exception("Startup broker reconcile failed — continuing without reconciliation")

    if broker_reconcile_summary.get("halted"):
        logger.critical(
            "Startup broker reconcile wrote HALTED.lock — aborting bot startup. "
            "Investigate broker state mismatch and delete HALTED.lock manually to resume."
        )
        sys.exit(1)

    # Liveness notification — fire after all startup reconcilers so we can
    # include both reconciler summaries in the message.
    try:
        from config import settings as _settings
        from notifications import notify
        notify(
            "critical",
            "Bot started",
            (
                f"trade-pilot started. "
                f"PendingOrders: {reconciler_summary}. "
                f"BrokerReconcile: {broker_reconcile_summary}. "
                f"Mode: {'DRY RUN' if _settings.DRY_RUN else 'LIVE'}"
            ),
            tags=["startup"],
        )
    except Exception:
        pass  # notification failure must not crash startup


# ── score_programmatic inline job ───────────────────────────


def _run_score_programmatic(args, settings) -> None:
    """Run the programmatic decision scorer for a date range.

    Controlled by EVALUATION_SCORER_ENABLED feature flag.  When the flag
    is off the job logs an info message and exits cleanly without error.
    """
    if not settings.EVALUATION_SCORER_ENABLED:
        logger.info(
            "score_programmatic: EVALUATION_SCORER_ENABLED is false — "
            "nothing to do. Set EVALUATION_SCORER_ENABLED=true to enable."
        )
        return

    from datetime import date, timedelta

    end_date = args.end_date or date.today().isoformat()
    start_date = args.start_date or (date.today() - timedelta(days=30)).isoformat()

    start_iso = f"{start_date}T00:00:00"
    end_iso = f"{end_date}T23:59:59"

    logger.info(
        "score_programmatic: scoring decisions from %s to %s", start_iso, end_iso
    )

    from database.db import Database
    from database.repositories.decision_scores_repository import DecisionScoresRepository
    from evaluation.scorer_programmatic import ProgrammaticScorer
    from evaluation.scoring_orchestrator import score_decisions_in_range

    db = Database(path=str(settings.DATABASE_PATH))
    db.init_schema()
    conn = db.get_connection()
    scores_repo = DecisionScoresRepository(conn)
    scorer = ProgrammaticScorer()

    count = score_decisions_in_range(start_iso, end_iso, scorer, scores_repo)
    logger.info("score_programmatic: wrote %d score rows", count)
    db.close()


# ── score_judge inline job ───────────────────────────────────


def _run_score_judge(args, settings) -> None:
    """Run the LLM-judge decision scorer for a date range.

    Controlled by EVALUATION_JUDGE_ENABLED feature flag.  When the flag is
    off the job logs an info message and exits cleanly without error.

    Pass --dry-run to print scores without writing to the database.
    """
    if not settings.EVALUATION_JUDGE_ENABLED:
        logger.info(
            "score_judge: EVALUATION_JUDGE_ENABLED is false — "
            "nothing to do. Set EVALUATION_JUDGE_ENABLED=true to enable."
        )
        return

    from datetime import date, timedelta

    end_date = args.end_date or date.today().isoformat()
    start_date = args.start_date or (date.today() - timedelta(days=30)).isoformat()

    start_iso = f"{start_date}T00:00:00"
    end_iso = f"{end_date}T23:59:59"

    dry_run = getattr(args, "dry_run", False) or settings.DRY_RUN

    logger.info(
        "score_judge: scoring decisions from %s to %s "
        "(model=%s, rate_limit_ms=%d, dry_run=%s)",
        start_iso,
        end_iso,
        settings.JUDGE_MODEL,
        settings.JUDGE_RATE_LIMIT_MS,
        dry_run,
    )

    import anthropic as _anthropic
    from database.db import Database
    from database.repositories.decision_scores_repository import DecisionScoresRepository
    from evaluation.scorer_judge import JudgeScorer
    from evaluation.scoring_orchestrator import score_decisions_with_judge

    db = Database(path=str(settings.DATABASE_PATH))
    db.init_schema()
    conn = db.get_connection()
    scores_repo = DecisionScoresRepository(conn)
    scorer = JudgeScorer(
        model=settings.JUDGE_MODEL,
        client=_anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY),
    )

    count = score_decisions_with_judge(
        start_iso,
        end_iso,
        scorer,
        scores_repo,
        rate_limit_ms=settings.JUDGE_RATE_LIMIT_MS,
        dry_run=dry_run,
    )
    logger.info(
        "score_judge: %s %d score rows",
        "would write" if dry_run else "wrote",
        count,
    )
    db.close()


# ── monthly_evaluation inline job ───────────────────────────


def _run_monthly_evaluation(args, settings) -> None:
    """Run the monthly evaluation pipeline for a specific or prior month.

    Controlled by EVALUATION_AUTOMATION_ENABLED feature flag.  When the flag
    is off the job logs an info message and exits cleanly without error.

    Pass --month YYYY-MM to backfill or regenerate a specific month.
    Regeneration uses an UPSERT pattern so no duplicate rows are created.
    """
    if not settings.EVALUATION_AUTOMATION_ENABLED:
        logger.info(
            "monthly_evaluation: EVALUATION_AUTOMATION_ENABLED is false — "
            "nothing to do. Set EVALUATION_AUTOMATION_ENABLED=true to enable."
        )
        return

    month = getattr(args, "month", None)
    from jobs.monthly_evaluation import run as _eval_run

    _eval_run(month=month)


# ── main ────────────────────────────────────────────────────


def main() -> None:
    args = parse_args()

    from config import settings

    # Apply CLI overrides to settings
    if args.dry_run:
        settings.DRY_RUN = True

    if args.symbol:
        settings.WATCHLIST = [args.symbol.upper()]

    if args.force_restart_sweep:
        os.environ["FORCE_RESTART_SWEEP"] = "1"

    # Configure logging
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=settings.LOG_DIR / "trade-pilot.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_fmt))

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(log_format, datefmt=date_fmt))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[stream_handler, file_handler],
    )

    # ── Structured API call log (api_calls.jsonl) ────────────────────
    # One JSON line per outbound API call.  propagate=False so these
    # lines do NOT bleed into trade-pilot.log.
    from utils.json_log_formatter import JsonFormatter

    _api_calls_handler = logging.handlers.TimedRotatingFileHandler(
        filename=settings.LOG_DIR / "api_calls.jsonl",
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    _api_calls_handler.setFormatter(JsonFormatter())
    _api_calls_logger = logging.getLogger("api_calls")
    _api_calls_logger.setLevel(logging.INFO)
    _api_calls_logger.addHandler(_api_calls_handler)
    _api_calls_logger.propagate = False

    # Silence per-request INFO noise from ORATS modules so trade-pilot.log
    # is not flooded by individual API call lines (moved to api_calls.jsonl).
    logging.getLogger("data.orats_historical").setLevel(logging.WARNING)
    logging.getLogger("data.orats_client").setLevel(logging.WARNING)

    # Structured JSONL log capture — tees WARNING+ to a rolling daily file.
    if settings.STRUCTURED_LOG_CAPTURE_ENABLED:
        from utils.structured_log_handler import install_structured_log_handler
        install_structured_log_handler(
            log_dir=settings.STRUCTURED_LOG_DIR,
            backup_count=settings.STRUCTURED_LOG_RETENTION_DAYS,
        )

    logger.info("Mode: %s", "PRODUCTION" if settings.RENDER else "LOCAL")
    logger.info("Dry Run: %s", settings.DRY_RUN)
    logger.info("Watchlist: %s", settings.WATCHLIST)

    # Startup validation (always runs)
    validate_startup()

    # ── Job mode: run once and exit ─────────────────────────
    if args.job:
        from utils.process_lock import ProcessLock, ProcessLockHeld

        # Expose job name so the API ledger (Phase 2) can tag each ORATS call.
        os.environ["TRADE_PILOT_JOB_NAME"] = args.job

        _lock_dir = settings.DATA_DIR / "locks"
        _job_lock = ProcessLock(f"job-{args.job}", _lock_dir)
        try:
            _job_lock.acquire()
        except ProcessLockHeld as _e:
            logger.error(
                "Job '%s' is already running (held by PID %d, lock: %s) — exiting",
                args.job,
                _e.holder_pid,
                _lock_dir / f"job-{args.job}.lock",
            )
            print(
                f"ERROR: job '{args.job}' is already running "
                f"(PID {_e.holder_pid}). Exiting.",
                file=sys.stderr,
            )
            sys.exit(2)

        try:
            logger.info("Running job: %s", args.job)

            if args.job == "score_programmatic":
                _run_score_programmatic(args, settings)
            elif args.job == "score_judge":
                _run_score_judge(args, settings)
            elif args.job == "monthly_evaluation":
                _run_monthly_evaluation(args, settings)
            else:
                module = importlib.import_module(JOB_MODULES[args.job])
                module.run()

            logger.info("Job %s finished — exiting", args.job)
        finally:
            _job_lock.release()
        return

    # ── Scheduler mode: start the loop ──────────────────────
    # Require explicit opt-in so the scheduler never starts on a local dev
    # machine by accident (e.g. leftover terminal, VS Code run button).
    # Set ALLOW_SCHEDULER=1 in Render's environment variables only.
    if not os.environ.get("ALLOW_SCHEDULER"):
        logger.error(
            "Scheduler refused to start: ALLOW_SCHEDULER env var is not set. "
            "Set ALLOW_SCHEDULER=1 in production (Render). "
            "To run a single job locally use: python main.py --job <name>"
        )
        print(
            "ERROR: ALLOW_SCHEDULER=1 is required to start the scheduler.\n"
            "To run a single job: python main.py --job <name>",
            file=sys.stderr,
        )
        sys.exit(1)

    from utils.process_lock import ProcessLock, ProcessLockHeld
    from scheduler import register_jobs, is_weekday, safe_run
    from jobs import pre_market
    import schedule

    _lock_dir = settings.DATA_DIR / "locks"
    _sched_lock = ProcessLock("scheduler", _lock_dir)
    try:
        _sched_lock.acquire()
    except ProcessLockHeld as _e:
        logger.error(
            "Scheduler is already running (held by PID %d, lock: %s) — exiting",
            _e.holder_pid,
            _lock_dir / "scheduler.lock",
        )
        print(
            f"ERROR: scheduler is already running (PID {_e.holder_pid}). Exiting.",
            file=sys.stderr,
        )
        sys.exit(2)

    logger.info("=== trade-pilot scheduler starting ===")

    register_jobs()

    logger.info("Scheduled jobs: %d", len(schedule.jobs))

    # Run pre-market immediately on startup if it's a weekday
    # (handles scheduler restarts mid-day)
    if is_weekday():
        logger.info("Weekday detected — running startup check")
        safe_run(pre_market.run, "pre_market")

    import time

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutdown requested — exiting.")
