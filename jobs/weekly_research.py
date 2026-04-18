"""Weekly research job — runs Sunday mornings.

Responsibilities:
  1. Liquidity scan (scan_all) — collect fresh option chain snapshots.
  2. Liquidity rescore (rescore_all) — recompute composite scores and tiers.
  3. Backtest sweep chunk — run backtests for the next chunk of symbols and
     accumulate symbol_strategy_stats / regime_strategy_stats.

Designed to be idempotent: re-running on the same Sunday is safe.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


def _run_preflight_check(sweep, ledger) -> None:
    """Run ORATS budget pre-flight check before the backtest sweep.

    Logs a structured one-line report then exits with code 3 if the estimated
    call count would exceed the remaining monthly budget (or 80% of it without
    the ORATS_ALLOW_BUDGET_HEAVY override).

    Args:
        sweep: BacktestSweep instance (provides estimate_cost()).
        ledger: ApiLedger instance (provides get_usage()).
    """
    estimate = sweep.estimate_cost()
    usage = ledger.get_usage("orats_historical")
    month_remaining = usage["month_remaining"]
    warm_estimate = estimate["warm_estimate"]

    logger.info(
        "ORATS pre-flight: cold_estimate=%d warm_estimate=%d "
        "month_used=%d month_cap=%d month_remaining=%d day_remaining=%d",
        estimate["cold_estimate"],
        warm_estimate,
        usage["month_used"],
        usage["month_cap"],
        month_remaining,
        usage["day_remaining"],
    )

    if warm_estimate > month_remaining:
        logger.error(
            "Pre-flight ABORT: warm_estimate=%d exceeds month_remaining=%d. "
            "The sweep would exhaust the ORATS monthly budget.",
            warm_estimate, month_remaining,
        )
        try:
            from notifications import notify
            notify(
                "critical",
                "weekly_research pre-flight aborted",
                f"ORATS estimate {warm_estimate:,} > remaining {month_remaining:,}. "
                "Sweep blocked.",
                tags=["orats", "pre_flight", "budget"],
            )
        except Exception:
            pass
        sys.exit(3)

    if warm_estimate > month_remaining * 0.8 and settings.ORATS_ALLOW_BUDGET_HEAVY != 1:
        logger.error(
            "Pre-flight ABORT: warm_estimate=%d > 80%% of month_remaining=%d. "
            "Set ORATS_ALLOW_BUDGET_HEAVY=1 to proceed anyway.",
            warm_estimate, month_remaining,
        )
        sys.exit(3)


def run() -> None:
    """Execute the weekly research pipeline."""
    logger.info("=== WEEKLY RESEARCH JOB STARTING ===")

    from database.db import Database
    from research.candidates.universe import CandidateUniverse

    db = Database()
    db.init_schema()

    universe = CandidateUniverse()
    universe.load()

    scan_stats: dict = {}
    rescore_stats: dict = {}

    from data.orats_usage_tracker import ORATSUsageTracker
    _usage_tracker = ORATSUsageTracker()

    # ── Phase 1: liquidity scan ───────────────────────────────────────────
    phase1_start = datetime.now(timezone.utc)
    try:
        from research.liquidity.scanner import LiquidityScanner
        from database.repositories import LiquidityRepository

        liq_repo = LiquidityRepository(db.get_connection())
        scanner = LiquidityScanner(repo=liq_repo, universe=universe)
        logger.info("Phase 1a: running liquidity scan")
        scan_stats = scanner.scan_all()
        logger.info("Liquidity scan complete: %s", scan_stats)
    except ImportError:
        logger.warning("LiquidityScanner not available — skipping scan step")
    except Exception:
        logger.exception("Liquidity scan failed — continuing")
        scan_stats = {"error": True}

    # Record phase 1 ORATS usage if client is accessible
    try:
        _orats_client_ref = None
        try:
            from data.orats_client import ORATSClient as _OC
            # The scanner may have used a module-level or instance-level client;
            # record what we can from scanner's internal client if accessible.
            if hasattr(scanner, '_orats') and hasattr(scanner._orats, 'get_usage'):
                _orats_client_ref = scanner._orats
        except Exception:
            pass
        if _orats_client_ref:
            _u = _orats_client_ref.get_usage()
            _usage_tracker.record_run(
                run_type="liquidity_scan",
                call_count=_u["total_calls"],
                by_endpoint=_u["by_endpoint"],
                started_at=phase1_start.replace(tzinfo=None),
                completed_at=datetime.utcnow(),
            )
            _orats_client_ref.reset_usage()
    except Exception:
        logger.debug("Could not record phase 1 ORATS usage", exc_info=True)

    # ── Phase 1: liquidity rescore ────────────────────────────────────────
    try:
        from research.liquidity.scanner import LiquidityScanner
        from database.repositories import LiquidityRepository

        liq_repo = LiquidityRepository(db.get_connection())
        scanner = LiquidityScanner(repo=liq_repo, universe=universe)
        logger.info("Phase 1b: recomputing liquidity scores")
        rescore_stats = scanner.rescore_all()
        logger.info("Liquidity rescore complete: %s", rescore_stats)
    except ImportError:
        logger.warning("LiquidityScanner not available — skipping rescore step")
    except Exception:
        logger.exception("Liquidity rescore failed — continuing")
        rescore_stats = {"error": True}

    # ── Phase 2: backtest sweep chunk ─────────────────────────────────────
    logger.info("Phase 2: running backtest sweep chunk")
    sweep_stats: dict = {}
    phase2_start = datetime.now(timezone.utc)
    try:
        from research.backtesting.sweep import BacktestSweep
        from backtesting.engine import BacktestEngine
        from database.repositories import BacktestStatsRepository

        bt_repo = BacktestStatsRepository(db.get_connection())
        _bt_engine = BacktestEngine()
        sweep = BacktestSweep(
            engine=_bt_engine,
            repo=bt_repo,
            universe=universe,
        )
        # ── Pre-flight budget check ──────────────────────────────────────
        try:
            from data.api_ledger import get_ledger
            _run_preflight_check(sweep, get_ledger())
        except SystemExit:
            raise  # propagate sys.exit(3) without logging it as a sweep failure
        except Exception:
            logger.warning("Pre-flight check failed (non-fatal) — proceeding", exc_info=True)

        _force_restart = os.environ.get("FORCE_RESTART_SWEEP", "0") == "1"
        sweep_stats = sweep.run_sweep(
            mode=settings.RESEARCH_BACKTEST_SWEEP_MODE,
            progress_cb=lambda msg: logger.info("bt_sweep: %s", msg),
            force_restart=_force_restart,
        )
        logger.info("Backtest sweep complete: %s", sweep_stats)
        # Record phase 2 ORATS usage if engine exposes an orats client
        try:
            _eng_orats = None
            for attr in ('orats_client', '_orats', 'orats'):
                if hasattr(_bt_engine, attr):
                    _eng_orats = getattr(_bt_engine, attr)
                    break
            if _eng_orats and hasattr(_eng_orats, 'get_usage'):
                _u2 = _eng_orats.get_usage()
                _usage_tracker.record_run(
                    run_type="backtest_sweep",
                    call_count=_u2["total_calls"],
                    by_endpoint=_u2["by_endpoint"],
                    started_at=phase2_start.replace(tzinfo=None),
                    completed_at=datetime.utcnow(),
                )
                _eng_orats.reset_usage()
        except Exception:
            logger.debug("Could not record phase 2 ORATS usage", exc_info=True)
    except Exception:
        logger.exception("Backtest sweep failed — continuing")
        sweep_stats = {"error": True}

    # ── Phase 3: watchlist recommendations ───────────────────────────────
    logger.info("Phase 3: generating watchlist recommendations")
    rec_summary: dict = {}
    try:
        if settings.RESEARCH_RECOMMENDATIONS_ENABLED:
            from research.recommendations.recommender import WatchlistRecommender
            from database.repositories import (
                RecommendationRepository,
                BacktestStatsRepository,
                LiquidityRepository,
            )

            liq_repo = LiquidityRepository(db.get_connection())
            bt_repo = BacktestStatsRepository(db.get_connection())
            rec_repo = RecommendationRepository(db.get_connection())

            recommender = WatchlistRecommender(
                liquidity_repo=liq_repo,
                backtest_stats_repo=bt_repo,
                universe=universe,
                candidate_universe=universe,
            )
            rec_results = recommender.generate_all()
            recommender.persist(rec_results, rec_repo)

            rec_summary = {
                wl: {
                    "add_count": len(data.get("add", [])),
                    "remove_count": len(data.get("remove", [])),
                }
                for wl, data in rec_results.items()
                if wl != "generated_at"
            }
            logger.info("Recommendations generated: %s", rec_summary)
        else:
            logger.info("Recommendations disabled by config")
            rec_summary = {"disabled": True}
    except Exception:
        logger.exception("Recommendation generation failed — continuing")
        rec_summary = {"error": True}

    # ── Phase 4: compute recommendation outcomes ──────────────────────────
    logger.info("Phase 4: computing recommendation outcomes")
    outcome_summary: dict = {}
    phase4_start = datetime.now(timezone.utc)
    try:
        from research.recommendations.outcomes import OutcomeComputer
        from database.repositories import (
            TradeRepository,
            RecommendationRepository,
        )
        from database.repositories.outcome_repository import OutcomeRepository
        from datetime import date as _date

        trade_repo_p4 = TradeRepository(db.get_connection())
        rec_repo_p4 = RecommendationRepository(db.get_connection())
        outcome_repo_p4 = OutcomeRepository(db.get_connection())

        # BacktestEngine may not be available in all environments
        try:
            from backtesting.engine import BacktestEngine as _BE
            bt_engine_p4 = _BE()
        except Exception:
            bt_engine_p4 = None

        computer = OutcomeComputer(
            trade_repo=trade_repo_p4,
            backtest_engine=bt_engine_p4,
            outcome_repo=outcome_repo_p4,
            rec_repo=rec_repo_p4,
        )
        outcome_summary = computer.compute_pending_outcomes(as_of_date=_date.today())
        logger.info("Outcome computation complete: %s", outcome_summary)
        # Record phase 4 ORATS usage if computer exposes an orats client
        try:
            _comp_orats = None
            for attr in ('orats_client', '_orats', 'orats'):
                if hasattr(computer, attr):
                    _comp_orats = getattr(computer, attr)
                    break
            if _comp_orats and hasattr(_comp_orats, 'get_usage'):
                _u4 = _comp_orats.get_usage()
                _usage_tracker.record_run(
                    run_type="outcome_computation",
                    call_count=_u4["total_calls"],
                    by_endpoint=_u4["by_endpoint"],
                    started_at=phase4_start.replace(tzinfo=None),
                    completed_at=datetime.utcnow(),
                )
                _comp_orats.reset_usage()
        except Exception:
            logger.debug("Could not record phase 4 ORATS usage", exc_info=True)
    except Exception:
        logger.exception("Phase 4 outcome computation failed — continuing")
        outcome_summary = {"error": True}

    # ── Persist run summary ───────────────────────────────────────────────
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scan_stats": scan_stats,
        "rescore_stats": rescore_stats,
        "sweep_stats": sweep_stats,
        "rec_summary": rec_summary,
        "outcome_summary": outcome_summary,
    }

    summary_path: Path = settings.DATA_DIR / "research_last_run.json"
    try:
        summary_path.write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        logger.info("Research run summary saved to %s", summary_path)
    except Exception:
        logger.warning("Could not write research_last_run.json", exc_info=True)

    logger.info("=== WEEKLY RESEARCH JOB COMPLETE ===")
