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
from datetime import datetime, timezone
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


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

    # ── Phase 1: liquidity scan ───────────────────────────────────────────
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
    try:
        from research.backtesting.sweep import BacktestSweep
        from backtesting.engine import BacktestEngine
        from database.repositories import BacktestStatsRepository

        bt_repo = BacktestStatsRepository(db.get_connection())
        sweep = BacktestSweep(
            engine=BacktestEngine(),
            repo=bt_repo,
            universe=universe,
        )
        sweep_stats = sweep.run_sweep(
            mode=settings.RESEARCH_BACKTEST_SWEEP_MODE,
            progress_cb=lambda msg: logger.info("bt_sweep: %s", msg),
        )
        logger.info("Backtest sweep complete: %s", sweep_stats)
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
