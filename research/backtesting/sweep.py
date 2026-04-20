"""BacktestSweep — weekly backtest data collection for the research layer.

Runs backtests for each (symbol × strategy) combination and stores raw
trades plus aggregated stats in the database. Designed to run as a
chunked Sunday job: each invocation processes up to MAX_SYMBOLS_PER_RUN
symbols, persisting state so the next Sunday picks up where it left off.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Strategy-specific backtest params derived from thresholds.STRATEGY_STRIKE_RANGES.
# delta = midpoint of (delta_min, delta_max) rounded to 0.05.
_STRATEGY_PARAMS: dict[str, dict] = {
    "wheel_csp": {
        "delta": 0.25,
        "dte_min": 21,
        "dte_max": 35,
        "ivr_threshold": 30.0,
    },
    "bull_put_spread": {
        "delta": 0.25,
        "dte_min": 21,
        "dte_max": 35,
        "ivr_threshold": 30.0,
    },
    "bear_call_spread": {
        "delta": 0.25,
        "dte_min": 21,
        "dte_max": 35,
        "ivr_threshold": 30.0,
    },
    "iron_condor": {
        "delta": 0.20,
        "dte_min": 20,
        "dte_max": 50,
        "ivr_threshold": 30.0,
    },
    "long_call_vertical": {
        "delta": 0.50,
        "dte_min": 30,
        "dte_max": 60,
        "ivr_threshold": 30.0,
    },
}


def _count_hist_summaries_cached(cache, symbol: str, max_days: int) -> int:
    """Count fresh hist/summaries cache entries for *symbol* in the ORATS cache table.

    Returns the number of distinct (symbol, date) summary rows that are still
    within their TTL — used by :meth:`BacktestSweep.estimate_cost` to compute
    the warm-cache call estimate.
    """
    if cache._conn is None:
        return 0
    import time as _time

    now = _time.time()
    try:
        row = cache._conn.execute(
            """
            SELECT COUNT(*) FROM orats_cache
            WHERE endpoint = 'hist/summaries'
              AND cache_key LIKE ?
              AND (? - fetched_at) < ttl_seconds
            """,
            (f"{symbol}|%", now),
        ).fetchone()
        return min(int(row[0]) if row else 0, max_days)
    except Exception:
        logger.warning("_count_hist_summaries_cached failed for %s", symbol, exc_info=True)
        return 0


class BacktestSweep:
    """Orchestrates weekly backtest sweeps and aggregated stats computation.

    Args:
        engine: A BacktestEngine instance used to run individual backtests.
        repo: BacktestStatsRepository for persistence.
        universe: CandidateUniverse used when sweep_mode='universe'.
        data_dir: Directory for state file; defaults to settings.DATA_DIR.
    """

    def __init__(self, engine, repo, universe, data_dir=None):
        self._engine = engine
        self._repo = repo
        self._universe = universe

        if data_dir is None:
            from config import settings
            self._data_dir = settings.DATA_DIR
        else:
            self._data_dir = Path(data_dir)

        self._state_path = self._data_dir / "backtest_sweep_state.json"

    # ── Cost estimation ──────────────────────────────────────────────────────

    def estimate_cost(self) -> dict:
        """Estimate ORATS calls needed given current cache state.

        Returns ``{'cold_estimate': int, 'warm_estimate': int, 'by_symbol': dict}``.

        Cold estimate: assumes no cache hits (~2,170 calls per (symbol × strategy)
        pair, consistent with the 2026-04-17 forensic report).

        Warm estimate: credits ``hist/summaries`` cache hits (fresh within the
        7-day TTL) as a proxy for fully-cached days; strikes and cores are
        not modeled separately — the estimate rounds up pessimistically.

        Lookback: ``RESEARCH_BACKTEST_LOOKBACK_YEARS × 252`` trading days.
        """
        from config import settings

        lookback_days = settings.RESEARCH_BACKTEST_LOOKBACK_YEARS * 252
        cold_cost_per_pair = 2170  # per forensic report 2026-04-17

        # Determine symbol list (mirror run_sweep logic)
        mode = settings.RESEARCH_BACKTEST_SWEEP_MODE
        if mode == "watchlist":
            symbols = sorted(set(
                list(settings.WATCHLIST)
                + list(settings.IRON_CONDOR_WATCHLIST)
                + list(settings.SPREAD_WATCHLIST)
            ))
        else:
            try:
                self._universe.load()
                symbols = self._universe.all_symbols()
            except Exception:
                logger.warning("estimate_cost: universe.all_symbols() failed; using watchlist")
                symbols = sorted(set(
                    list(settings.WATCHLIST)
                    + list(settings.IRON_CONDOR_WATCHLIST)
                    + list(settings.SPREAD_WATCHLIST)
                ))

        strategies = list(_STRATEGY_PARAMS.keys())
        n_strategies = len(strategies)

        cold_total = len(symbols) * n_strategies * cold_cost_per_pair

        # Query the ORATS cache to credit already-cached hist/summaries days
        from data.orats_cache import ORATSCache
        import time as _time

        _cache = ORATSCache()
        by_symbol: dict = {}
        warm_total = 0

        for sym in symbols:
            fresh = _count_hist_summaries_cached(_cache, sym.upper(), lookback_days)
            cached_frac = min(fresh / lookback_days, 1.0) if lookback_days > 0 else 0.0
            warm_per_pair = int(cold_cost_per_pair * (1.0 - cached_frac))
            sym_warm = warm_per_pair * n_strategies
            by_symbol[sym] = {
                "cold": cold_cost_per_pair * n_strategies,
                "warm": sym_warm,
                "cached_summary_days": fresh,
                "cached_fraction": round(cached_frac, 3),
            }
            warm_total += sym_warm

        return {
            "cold_estimate": cold_total,
            "warm_estimate": warm_total,
            "by_symbol": by_symbol,
        }

    # ── State file helpers ────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Could not parse backtest_sweep_state.json; resetting")
        return {
            "last_run_id": None,
            "last_completed_at": None,
            "completed_symbols": {},
            "current_run_id": None,
            "mode": None,
        }

    def _save_state(self, state: dict) -> None:
        self._state_path.write_text(
            json.dumps(state, indent=2, default=str), encoding="utf-8"
        )

    # ── Core sweep ────────────────────────────────────────────────────────

    def sweep_symbols(
        self,
        symbols: list[str],
        strategies: list[str],
        start_date: str,
        end_date: str,
        sweep_run_id: str,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Run backtests for every (strategy × symbol) pair.

        Returns summary dict with total_attempts, successes, failures,
        trades_inserted.
        """
        from backtesting.engine import BacktestParams

        total_attempts = 0
        successes = 0
        failures = 0
        trades_inserted = 0

        for strategy in strategies:
            params_defaults = _STRATEGY_PARAMS.get(strategy, {})
            for symbol in symbols:
                total_attempts += 1
                msg = f"{strategy}/{symbol} [{start_date}→{end_date}]"
                if progress_cb:
                    progress_cb(f"sweeping {msg}")

                try:
                    params = BacktestParams(
                        strategy=strategy,
                        symbols=[symbol],
                        start_date=start_date,
                        end_date=end_date,
                        delta=params_defaults.get("delta", 0.25),
                        dte_min=params_defaults.get("dte_min", 21),
                        dte_max=params_defaults.get("dte_max", 35),
                        ivr_threshold=params_defaults.get("ivr_threshold", 30.0),
                    )
                    result = self._engine.run(params)

                    if result.trades:
                        trade_dicts = [
                            _simulated_trade_to_dict(t, strategy, symbol)
                            for t in result.trades
                        ]
                        n = self._repo.insert_trades_batch(trade_dicts, sweep_run_id)
                        trades_inserted += n

                    successes += 1
                    if progress_cb:
                        progress_cb(
                            f"  done {msg}: {len(result.trades)} trades"
                        )

                except Exception:
                    failures += 1
                    logger.exception("Backtest sweep failed for %s", msg)

        return {
            "total_attempts": total_attempts,
            "successes": successes,
            "failures": failures,
            "trades_inserted": trades_inserted,
        }

    # ── Stats recomputation ───────────────────────────────────────────────

    def recompute_symbol_stats(self, lookback_years: int) -> dict:
        """Recompute symbol_strategy_stats from raw backtest_trades.

        Returns {strategy_type: count_stats_written}.
        """
        from config import settings

        cutoff = (date.today() - timedelta(days=lookback_years * 365)).isoformat()
        end_date_str = date.today().isoformat()

        # Gather all distinct (symbol, strategy_type) pairs in the window
        rows = self._repo.get_trades(since_date=cutoff)
        pairs: set[tuple[str, str]] = {
            (r["symbol"], r["strategy_type"]) for r in rows
        }

        counts: dict[str, int] = {}
        for symbol, strategy_type in sorted(pairs):
            trades = [
                r for r in rows
                if r["symbol"] == symbol and r["strategy_type"] == strategy_type
            ]
            if not trades:
                continue

            pnl_series = [t["pnl"] for t in trades if t.get("pnl") is not None]
            if not pnl_series:
                continue

            trade_count = len(pnl_series)
            win_count = sum(1 for p in pnl_series if p > 0)
            win_rate = win_count / trade_count
            total_pnl = sum(pnl_series)
            avg_pnl = total_pnl / trade_count
            max_drawdown = _compute_max_drawdown(pnl_series)
            sharpe = _compute_sharpe(pnl_series)

            hi = settings.RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE
            lo = settings.RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE
            if trade_count >= hi:
                confidence = "high"
            elif trade_count >= lo:
                confidence = "low"
            else:
                confidence = "none"

            self._repo.upsert_symbol_stat({
                "symbol": symbol,
                "strategy_type": strategy_type,
                "trade_count": trade_count,
                "win_count": win_count,
                "win_rate": win_rate,
                "avg_pnl_per_trade": avg_pnl,
                "total_pnl": total_pnl,
                "max_drawdown": max_drawdown,
                "sharpe_ratio": sharpe,
                "confidence": confidence,
                "date_range_start": cutoff,
                "date_range_end": end_date_str,
            })
            counts[strategy_type] = counts.get(strategy_type, 0) + 1

        return counts

    def recompute_regime_stats(self, lookback_years: int) -> dict:
        """Recompute regime_strategy_stats from raw backtest_trades.

        Returns {strategy_type: count_stats_written}.
        """
        from config import settings

        cutoff = (date.today() - timedelta(days=lookback_years * 365)).isoformat()
        rows = self._repo.get_trades(since_date=cutoff)

        # Bucket by (entry_regime, strategy_type)
        buckets: dict[tuple[str, str], list[float]] = {}
        for r in rows:
            regime = r.get("entry_regime")
            strategy = r.get("strategy_type")
            pnl = r.get("pnl")
            if regime and strategy and pnl is not None:
                key = (regime, strategy)
                buckets.setdefault(key, []).append(pnl)

        min_trades = settings.RESEARCH_BACKTEST_REGIME_MIN_TRADES
        counts: dict[str, int] = {}

        for (regime, strategy_type), pnl_series in sorted(buckets.items()):
            trade_count = len(pnl_series)
            win_count = sum(1 for p in pnl_series if p > 0)
            win_rate = win_count / trade_count
            total_pnl = sum(pnl_series)
            avg_pnl = total_pnl / trade_count
            max_drawdown = _compute_max_drawdown(pnl_series)
            confidence = "high" if trade_count >= min_trades else "low"

            self._repo.upsert_regime_stat({
                "entry_regime": regime,
                "strategy_type": strategy_type,
                "trade_count": trade_count,
                "win_count": win_count,
                "win_rate": win_rate,
                "avg_pnl_per_trade": avg_pnl,
                "total_pnl": total_pnl,
                "max_drawdown": max_drawdown,
                "confidence": confidence,
            })
            counts[strategy_type] = counts.get(strategy_type, 0) + 1

        return counts

    # ── sweep_progress table helpers ─────────────────────────────────────────

    def _sweep_db_conn(self):
        """Return a dedicated sqlite3 connection to the main database."""
        import sqlite3 as _sqlite3
        from config import settings as _settings
        conn = _sqlite3.connect(str(_settings.DATABASE_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _load_sweep_progress(
        self,
        symbols: list[str],
        strategies: list[str],
        lookback_years: int,
    ) -> dict[tuple[str, str], dict]:
        """Load sweep_progress rows for the given pairs.

        Returns a dict keyed by (symbol, strategy) → {last_primed_at, prime_cost_calls, last_error}.
        """
        result: dict[tuple[str, str], dict] = {}
        try:
            conn = self._sweep_db_conn()
            try:
                placeholders_sym = ",".join("?" * len(symbols))
                placeholders_strat = ",".join("?" * len(strategies))
                rows = conn.execute(
                    f"""
                    SELECT symbol, strategy, last_primed_at, prime_cost_calls, last_error
                    FROM sweep_progress
                    WHERE symbol IN ({placeholders_sym})
                      AND strategy IN ({placeholders_strat})
                      AND lookback_years = ?
                    """,
                    [*symbols, *strategies, lookback_years],
                ).fetchall()
                for sym, strat, primed_at, cost, error in rows:
                    result[(sym, strat)] = {
                        "last_primed_at": primed_at,
                        "prime_cost_calls": cost,
                        "last_error": error,
                    }
            finally:
                conn.close()
        except Exception:
            logger.warning("_load_sweep_progress failed", exc_info=True)
        return result

    def _update_sweep_progress(
        self,
        symbol: str,
        strategy: str,
        lookback_years: int,
        success: bool,
        cost_calls: int,
        error: Optional[str],
    ) -> None:
        """Upsert a sweep_progress row after processing a pair."""
        try:
            conn = self._sweep_db_conn()
            try:
                primed_at = time.time() if success else None
                conn.execute(
                    """
                    INSERT INTO sweep_progress (symbol, strategy, lookback_years,
                        last_primed_at, prime_cost_calls, last_error)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, strategy, lookback_years) DO UPDATE SET
                        last_primed_at   = excluded.last_primed_at,
                        prime_cost_calls = excluded.prime_cost_calls,
                        last_error       = excluded.last_error
                    """,
                    (symbol, strategy, lookback_years, primed_at, cost_calls, error),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.warning(
                "_update_sweep_progress failed for %s/%s", symbol, strategy, exc_info=True
            )

    def _reset_sweep_progress(self, symbols: list[str], strategies: list[str], lookback_years: int) -> None:
        """Clear sweep_progress for all matching pairs (used on force_restart)."""
        try:
            conn = self._sweep_db_conn()
            try:
                placeholders_sym = ",".join("?" * len(symbols))
                placeholders_strat = ",".join("?" * len(strategies))
                conn.execute(
                    f"""
                    DELETE FROM sweep_progress
                    WHERE symbol IN ({placeholders_sym})
                      AND strategy IN ({placeholders_strat})
                      AND lookback_years = ?
                    """,
                    [*symbols, *strategies, lookback_years],
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.warning("_reset_sweep_progress failed", exc_info=True)

    # ── Per-pair cost estimation ──────────────────────────────────────────────

    def estimate_pair_cost(self, symbol: str, strategy: str) -> int:
        """Estimate warm-cache ORATS calls for a single (symbol, strategy) pair.

        Returns an integer call count (0 if fully cached, up to cold_cost_per_pair).
        """
        from config import settings
        from data.orats_cache import ORATSCache

        lookback_days = settings.RESEARCH_BACKTEST_LOOKBACK_YEARS * 252
        cold_cost_per_pair = 2170

        _cache = ORATSCache()
        fresh = _count_hist_summaries_cached(_cache, symbol.upper(), lookback_days)
        cached_frac = min(fresh / lookback_days, 1.0) if lookback_days > 0 else 0.0
        return int(cold_cost_per_pair * (1.0 - cached_frac))

    # ── Priority queue ────────────────────────────────────────────────────────

    def _build_priority_queue(
        self,
        symbols: list[str],
        strategies: list[str],
        lookback_years: int,
        open_symbols: set[str],
        progress: dict[tuple[str, str], dict],
        reprime_weeks: int,
    ) -> list[tuple[str, str]]:
        """Return (symbol, strategy) pairs sorted by priority.

        Priority 1: symbol has an open Alpaca position
        Priority 2: pair never primed (null last_primed_at)
        Priority 3: pair primed > reprime_weeks ago
        Skip:       pair primed within reprime_weeks
        """
        reprime_cutoff = time.time() - reprime_weeks * 7 * 24 * 3600
        ranked: list[tuple[int, str, str]] = []

        for sym in symbols:
            for strat in strategies:
                row = progress.get((sym, strat), {})
                primed_at = row.get("last_primed_at")

                if primed_at is not None and primed_at >= reprime_cutoff:
                    # Primed recently — skip
                    continue

                if sym in open_symbols:
                    priority = 1
                elif primed_at is None:
                    priority = 2
                else:
                    priority = 3

                ranked.append((priority, sym, strat))

        ranked.sort(key=lambda t: t[0])
        return [(sym, strat) for _, sym, strat in ranked]

    # ── Top-level orchestration ───────────────────────────────────────────

    def run_sweep(
        self,
        mode: str,
        progress_cb: Optional[Callable[[str], None]] = None,
        force_restart: bool = False,
    ) -> dict:
        """Orchestrate one budget-aware rotating sweep chunk.

        Processes (symbol, strategy) pairs in priority order (open positions first,
        then unprimed, then stale) until the per-run budget or monthly ORATS budget
        is exhausted.  Progress is persisted in the sweep_progress database table so
        each run continues from where the previous one left off.

        Args:
            mode:          ``'watchlist'`` or ``'universe'``.
            progress_cb:   Optional callback called with a progress message
                           after each pair completes.
            force_restart: When True, clear sweep_progress for all pairs so the
                           next run treats everything as unprimed.

        Returns a summary dict compatible with the legacy run_sweep() shape.
        """
        from config import settings
        from backtesting.engine import SUPPORTED_STRATEGIES

        run_id = str(uuid.uuid4())
        lookback_years = settings.RESEARCH_BACKTEST_LOOKBACK_YEARS
        today = date.today().isoformat()
        end_date = today
        start_date = (date.today() - timedelta(days=lookback_years * 365)).isoformat()

        # ── 1. Determine symbol list ──────────────────────────────────────
        if mode == "watchlist":
            full_list = sorted(set(
                list(settings.WATCHLIST)
                + list(settings.IRON_CONDOR_WATCHLIST)
                + list(settings.SPREAD_WATCHLIST)
            ))
        else:
            try:
                self._universe.load()
                full_list = self._universe.all_symbols()
            except Exception:
                logger.warning("run_sweep: universe load failed; falling back to watchlist")
                full_list = sorted(set(
                    list(settings.WATCHLIST)
                    + list(settings.IRON_CONDOR_WATCHLIST)
                    + list(settings.SPREAD_WATCHLIST)
                ))

        strategies = list(SUPPORTED_STRATEGIES)

        # ── 2. Reset sweep_progress if force_restart ──────────────────────
        if force_restart:
            logger.info("force_restart=True — clearing sweep_progress for all pairs")
            self._reset_sweep_progress(full_list, strategies, lookback_years)

        # ── 3. Get open Alpaca positions (for priority 1) ─────────────────
        open_symbols: set[str] = set()
        try:
            from brokers.broker_factory import get_broker
            positions = get_broker().get_positions() or []
            open_symbols = {
                p.get("symbol", "").split("_")[0].split(" ")[0]
                for p in positions
                if p.get("symbol")
            }
        except Exception:
            logger.debug("run_sweep: could not fetch open positions for priority", exc_info=True)

        # ── 4. Load sweep_progress and build priority queue ───────────────
        progress = self._load_sweep_progress(full_list, strategies, lookback_years)
        pairs = self._build_priority_queue(
            symbols=full_list,
            strategies=strategies,
            lookback_years=lookback_years,
            open_symbols=open_symbols,
            progress=progress,
            reprime_weeks=settings.SWEEP_REPRIME_WEEKS,
        )

        # ── 5. Budget limits ──────────────────────────────────────────────
        run_budget = settings.WEEKLY_SWEEP_BUDGET_CALLS
        monthly_remaining = run_budget  # conservative default if ledger unavailable
        try:
            from data.api_ledger import get_ledger
            usage = get_ledger().get_usage("orats_historical")
            monthly_remaining = usage.get("month_remaining", run_budget)
        except Exception:
            logger.debug("run_sweep: could not read monthly budget from ledger", exc_info=True)

        # ── 6. Greedy fill ────────────────────────────────────────────────
        calls_used = 0
        pairs_primed = 0
        pairs_failed = 0
        pairs_skipped_budget = 0

        for symbol, strategy in pairs:
            estimated = self.estimate_pair_cost(symbol, strategy)
            remaining_run = run_budget - calls_used
            remaining_monthly = monthly_remaining - calls_used

            budget_limit = min(remaining_run, remaining_monthly)
            if estimated > budget_limit:
                pairs_skipped_budget += 1
                logger.debug(
                    "run_sweep: skipping %s/%s — estimated %d calls > remaining %d",
                    symbol, strategy, estimated, budget_limit,
                )
                break  # pairs are sorted; if this one doesn't fit, later ones won't either

            if progress_cb:
                progress_cb(f"priming {symbol}/{strategy}")

            try:
                self.sweep_symbols(
                    symbols=[symbol],
                    strategies=[strategy],
                    start_date=start_date,
                    end_date=end_date,
                    sweep_run_id=run_id,
                    progress_cb=progress_cb,
                )
                self._update_sweep_progress(
                    symbol, strategy, lookback_years,
                    success=True, cost_calls=estimated, error=None,
                )
                calls_used += estimated
                pairs_primed += 1
            except Exception as exc:
                error_msg = str(exc)[:500]
                self._update_sweep_progress(
                    symbol, strategy, lookback_years,
                    success=False, cost_calls=0, error=error_msg,
                )
                pairs_failed += 1
                logger.exception("run_sweep: failed to prime %s/%s", symbol, strategy)

        # Pairs remaining after this run (eligible but not processed)
        pairs_remaining = len(pairs) - pairs_primed - pairs_failed

        # ── 7. Recompute stats if all pairs primed ────────────────────────
        stats_result: dict = {}
        regime_result: dict = {}
        all_progress = self._load_sweep_progress(full_list, strategies, lookback_years)
        reprime_cutoff = time.time() - settings.SWEEP_REPRIME_WEEKS * 7 * 24 * 3600
        all_primed = all(
            (sym, strat) in all_progress
            and all_progress[(sym, strat)].get("last_primed_at") is not None
            and all_progress[(sym, strat)]["last_primed_at"] >= reprime_cutoff
            for sym in full_list for strat in strategies
        )
        # Recompute stats if this run primed any pairs. Even partial primes
        # advance the table — incomplete watchlists are better than empty
        # tables. Guard against empty inputs so recompute is never called with
        # zero work.
        if pairs_primed > 0 and full_list and strategies:
            logger.info(
                "Recomputing backtest stats (%d pair(s) primed this run; all_primed=%s)",
                pairs_primed, all_primed,
            )
            try:
                stats_result = self.recompute_symbol_stats(lookback_years)
                regime_result = self.recompute_regime_stats(lookback_years)
            except Exception:
                logger.exception("recompute_symbol_stats / recompute_regime_stats failed")

        # ── 8. Log summary + ntfy ─────────────────────────────────────────
        est_full_prime_weeks = (
            math.ceil(len(pairs) / max(pairs_primed, 1))
            if pairs_primed > 0
            else None
        )
        logger.info(
            "Weekly sweep: %d pairs primed, %d failed. "
            "Budget used: %d/%d calls. Pairs remaining: %d. "
            "Est. full prime: %s weeks.",
            pairs_primed, pairs_failed,
            calls_used, run_budget, pairs_remaining,
            est_full_prime_weeks if est_full_prime_weeks else "unknown",
        )

        if pairs_primed > 0:
            try:
                from notifications import notify
                notify(
                    "default",
                    "weekly sweep complete",
                    f"{pairs_primed} pairs primed, {pairs_failed} failed. "
                    f"Budget: {calls_used}/{run_budget} calls. "
                    f"Remaining: {pairs_remaining} pairs.",
                    tags=["sweep", "weekly_research"],
                )
            except Exception:
                pass

        return {
            "run_id": run_id,
            "mode": mode,
            "pairs_primed": pairs_primed,
            "pairs_failed": pairs_failed,
            "calls_used": calls_used,
            "pairs_remaining": pairs_remaining,
            # Legacy keys for backward compatibility
            "chunk_size": pairs_primed,
            "remaining_after": pairs_remaining,
            "sweep": {
                "total_attempts": pairs_primed + pairs_failed,
                "successes": pairs_primed,
                "failures": pairs_failed,
            },
            "symbol_stats": stats_result,
            "regime_stats": regime_result,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _simulated_trade_to_dict(trade, strategy_type: str, symbol: str) -> dict:
    """Convert a SimulatedTrade to the trade_data dict shape for insert_trades_batch."""
    d = asdict(trade) if hasattr(trade, "__dataclass_fields__") else dict(trade)
    return {
        "symbol": d.get("symbol", symbol),
        "strategy_type": d.get("strategy", strategy_type),
        "entry_date": d.get("entry_date", ""),
        "exit_date": d.get("exit_date"),
        "entry_credit": d.get("entry_credit"),
        "exit_debit": d.get("exit_debit"),
        "pnl": d.get("pnl"),
        "exit_reason": d.get("exit_reason"),
        "entry_delta": d.get("entry_delta"),
        "entry_ivr": d.get("entry_ivr"),
        "entry_regime": d.get("entry_regime"),
        "entry_iv_env": d.get("entry_iv_env"),
        "holding_days": d.get("holding_days"),
        "contracts": d.get("contracts", 1),
        "trade_json": d,
    }


def _compute_max_drawdown(pnl_series: list[float]) -> float:
    """Compute max drawdown from a sequential list of trade P&Ls."""
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnl_series:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _compute_sharpe(pnl_series: list[float]) -> Optional[float]:
    """Compute mean/std Sharpe ratio. Returns None if std == 0."""
    if not pnl_series:
        return None
    n = len(pnl_series)
    mean = sum(pnl_series) / n
    variance = sum((p - mean) ** 2 for p in pnl_series) / n
    std = math.sqrt(variance)
    if std == 0:
        return None
    return round(mean / std, 4)
