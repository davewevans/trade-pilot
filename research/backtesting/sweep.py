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
import uuid
from dataclasses import asdict
from datetime import date, timedelta
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

    # ── Top-level orchestration ───────────────────────────────────────────

    def run_sweep(
        self,
        mode: str,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Orchestrate one Sunday sweep chunk.

        Returns a summary dict including run_id, mode, symbols processed,
        and whether stats were recomputed.
        """
        from config import settings
        from backtesting.engine import SUPPORTED_STRATEGIES

        # ── 1. Determine symbol list ──────────────────────────────────────
        if mode == "watchlist":
            full_list = sorted(set(
                list(settings.WATCHLIST)
                + list(settings.IRON_CONDOR_WATCHLIST)
                + list(settings.SPREAD_WATCHLIST)
            ))
        else:
            self._universe.load()
            full_list = self._universe.all_symbols()

        # ── 2. Load or reset state ────────────────────────────────────────
        state = self._load_state()
        lookback_years = settings.RESEARCH_BACKTEST_LOOKBACK_YEARS

        today = date.today().isoformat()
        stale = False
        if state.get("last_completed_at"):
            last = state["last_completed_at"][:10]
            try:
                days_since = (date.fromisoformat(today) - date.fromisoformat(last)).days
                stale = days_since > 7
            except ValueError:
                stale = True

        mode_changed = state.get("mode") != mode

        if mode_changed or stale or state.get("current_run_id") is None:
            run_id = str(uuid.uuid4())
            state["current_run_id"] = run_id
            state["completed_symbols"] = {s: [] for s in SUPPORTED_STRATEGIES}
            state["mode"] = mode
            logger.info("Starting new backtest sweep run %s (mode=%s)", run_id, mode)
        else:
            run_id = state["current_run_id"]
            if not isinstance(state.get("completed_symbols"), dict):
                state["completed_symbols"] = {s: [] for s in SUPPORTED_STRATEGIES}
            logger.info("Resuming backtest sweep run %s (mode=%s)", run_id, mode)

        # ── 3. Determine remaining symbols ────────────────────────────────
        # Use union of remaining across all strategies (conservative approach:
        # a symbol is "done" only when all strategies have processed it)
        completed_sets = {
            strat: set(state["completed_symbols"].get(strat, []))
            for strat in SUPPORTED_STRATEGIES
        }
        remaining = [
            sym for sym in full_list
            if any(sym not in completed_sets[s] for s in SUPPORTED_STRATEGIES)
        ]

        # ── 4. Cap per run ────────────────────────────────────────────────
        max_per_run = settings.RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN
        chunk = remaining[:max_per_run]

        if not chunk:
            logger.info("All symbols already complete for run %s", run_id)
            stats_result = {}
            regime_result = {}
        else:
            # ── 5. Date range ─────────────────────────────────────────────
            end_date = today
            start_date = (
                date.today() - timedelta(days=lookback_years * 365)
            ).isoformat()

            # ── 6. Run the sweep ──────────────────────────────────────────
            sweep_result = self.sweep_symbols(
                symbols=chunk,
                strategies=SUPPORTED_STRATEGIES,
                start_date=start_date,
                end_date=end_date,
                sweep_run_id=run_id,
                progress_cb=progress_cb,
            )

            # ── 7. Update state with completed symbols ────────────────────
            for strat in SUPPORTED_STRATEGIES:
                done = state["completed_symbols"].get(strat, [])
                state["completed_symbols"][strat] = sorted(set(done) | set(chunk))
            self._save_state(state)

            # ── 8. If all symbols complete, recompute stats ───────────────
            remaining_after = [
                sym for sym in full_list
                if any(
                    sym not in set(state["completed_symbols"].get(s, []))
                    for s in SUPPORTED_STRATEGIES
                )
            ]
            stats_result = {}
            regime_result = {}
            if not remaining_after:
                logger.info("All symbols processed — recomputing stats")
                stats_result = self.recompute_symbol_stats(lookback_years)
                regime_result = self.recompute_regime_stats(lookback_years)
                state["last_run_id"] = run_id
                state["last_completed_at"] = today
                state["current_run_id"] = None
                self._save_state(state)

            return {
                "run_id": run_id,
                "mode": mode,
                "chunk_size": len(chunk),
                "remaining_after": len(remaining_after),
                "sweep": sweep_result,
                "symbol_stats": stats_result,
                "regime_stats": regime_result,
            }

        # No chunk needed (already complete)
        return {
            "run_id": run_id,
            "mode": mode,
            "chunk_size": 0,
            "remaining_after": 0,
            "sweep": {},
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
