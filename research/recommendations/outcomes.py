"""OutcomeComputer — compute recommendation accuracy outcomes.

Four cells based on (action, operator_decision):
  accepted add   → ground_truth_live
  rejected add   → proxy_backtest
  accepted remove → proxy_backtest
  rejected remove → ground_truth_live
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

WINDOW_DAYS = 90

# Watchlist → strategy types (must match recommender._WATCHLIST_STRATEGIES)
_WATCHLIST_STRATEGIES: dict[str, list[str]] = {
    "wheel": ["wheel_csp", "wheel_cc"],
    "iron_condor": ["iron_condor"],
    "iron_butterfly": ["iron_butterfly"],
    "spreads": ["bull_put_spread", "bear_call_spread", "long_call_vertical"],
}


def _strategy_params_version() -> str:
    """Hash of current _STRATEGY_PARAMS dict for auditability."""
    from research.backtesting.sweep import _STRATEGY_PARAMS
    content = json.dumps(_STRATEGY_PARAMS, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:12]


class OutcomeComputer:
    """Compute recommendation outcomes for the 4-cell accuracy scorecard.

    Args:
        trade_repo: TradeRepository for ground-truth live queries.
        backtest_engine: BacktestEngine for proxy backtest computations.
        outcome_repo: OutcomeRepository for persistence.
        rec_repo: RecommendationRepository to read decided recommendations.
    """

    def __init__(self, trade_repo, backtest_engine, outcome_repo, rec_repo):
        self._trade_repo = trade_repo
        self._engine = backtest_engine
        self._outcome_repo = outcome_repo
        self._rec_repo = rec_repo

    def compute_pending_outcomes(self, as_of_date: Optional[date] = None) -> dict:
        """Compute outcomes for all eligible recommendations.

        Eligibility:
        - action IN ('add', 'remove')
        - operator_decision IN ('accepted', 'rejected')
        - generated_at at least WINDOW_DAYS days ago
        - No existing row in recommendation_outcomes for this rec + window=90

        Returns summary dict.
        """
        if as_of_date is None:
            as_of_date = date.today()

        cutoff = (as_of_date - timedelta(days=WINDOW_DAYS)).isoformat()
        computed = 0
        skipped = 0
        errors = 0

        all_recs = self._rec_repo.get_history(limit=100000)

        for rec in all_recs:
            rec_id = rec.get("recommendation_id")
            action = rec.get("action")
            op_decision = rec.get("operator_decision")
            generated_at = (rec.get("generated_at") or "")[:10]

            # Skip if not eligible
            if action not in ("add", "remove"):
                continue
            if op_decision not in ("accepted", "rejected"):
                continue
            if generated_at >= cutoff:
                # Less than WINDOW_DAYS old — window hasn't closed yet
                skipped += 1
                continue
            if self._outcome_repo.exists(rec_id, WINDOW_DAYS):
                skipped += 1
                continue

            # Determine cell
            # accepted add → ground_truth_live
            # rejected add → proxy_backtest
            # accepted remove → proxy_backtest
            # rejected remove → ground_truth_live
            if (action == "add" and op_decision == "accepted") or \
               (action == "remove" and op_decision == "rejected"):
                outcome_type = "ground_truth_live"
            else:
                outcome_type = "proxy_backtest"

            symbol = rec.get("symbol", "").upper()
            watchlist_name = rec.get("watchlist_name", "")
            decision_at = (rec.get("operator_decided_at") or rec.get("generated_at") or "")[:10]

            try:
                if outcome_type == "ground_truth_live":
                    row = self._compute_ground_truth(
                        symbol, watchlist_name, decision_at, as_of_date
                    )
                else:
                    row = self._compute_proxy_backtest(
                        symbol, watchlist_name, decision_at, as_of_date
                    )

                row.update({
                    "recommendation_id": rec_id,
                    "outcome_type": outcome_type,
                    "window_days": WINDOW_DAYS,
                    "window_end_date": as_of_date.isoformat(),
                })
                self._outcome_repo.insert(row)
                computed += 1
            except Exception:
                logger.exception(
                    "Failed to compute outcome for rec_id=%s (%s/%s/%s)",
                    rec_id, symbol, watchlist_name, outcome_type
                )
                errors += 1

        return {"computed": computed, "skipped": skipped, "errors": errors}

    def _compute_ground_truth(
        self,
        symbol: str,
        watchlist_name: str,
        start_date: str,
        as_of_date: date,
    ) -> dict:
        """Query live trades in the 90-day window after decision_at."""
        from database.repositories.trades import trade_pnl

        strategy_types = _WATCHLIST_STRATEGIES.get(watchlist_name, [])
        end_date = as_of_date.isoformat()

        all_pnls: list[float] = []
        for strat in strategy_types:
            trades = self._trade_repo.get_closed_trades_in_window(
                symbol, strat, start_date, end_date
            )
            for t in trades:
                p = trade_pnl(t)
                if p is not None:
                    all_pnls.append(p)

        trade_count = len(all_pnls)
        pnl_total = round(sum(all_pnls), 2) if all_pnls else None
        pnl_per_trade = round(pnl_total / trade_count, 2) if all_pnls else None
        wins = sum(1 for p in all_pnls if p > 0)
        win_rate = round(wins / trade_count, 4) if all_pnls else None

        return {
            "trade_count": trade_count if all_pnls else 0,
            "pnl_total": pnl_total,
            "pnl_per_trade": pnl_per_trade,
            "win_rate": win_rate,
            "proxy_params_json": None,
        }

    def _compute_proxy_backtest(
        self,
        symbol: str,
        watchlist_name: str,
        start_date: str,
        as_of_date: date,
    ) -> dict:
        """Run backtest over the 90-day window using current strategy params."""
        from research.backtesting.sweep import _STRATEGY_PARAMS

        strategy_types = _WATCHLIST_STRATEGIES.get(watchlist_name, [])
        if not strategy_types:
            return {"trade_count": 0, "pnl_total": None, "pnl_per_trade": None, "win_rate": None, "proxy_params_json": None}

        # Use first strategy type as representative
        strategy = strategy_types[0]
        params_defaults = _STRATEGY_PARAMS.get(strategy, {})

        end_date = as_of_date.isoformat()

        proxy_params = {
            "strategy": strategy,
            "symbol": symbol,
            "delta": params_defaults.get("delta", 0.25),
            "dte_min": params_defaults.get("dte_min", 21),
            "dte_max": params_defaults.get("dte_max", 35),
            "ivr_threshold": params_defaults.get("ivr_threshold", 30.0),
            "strategy_params_version": _strategy_params_version(),
        }

        if self._engine is None:
            return {
                "trade_count": 0, "pnl_total": None, "pnl_per_trade": None,
                "win_rate": None, "proxy_params_json": proxy_params,
            }

        try:
            from backtesting.engine import BacktestParams
            params = BacktestParams(
                strategy=strategy,
                symbols=[symbol],
                start_date=start_date,
                end_date=end_date,
                delta=proxy_params["delta"],
                dte_min=proxy_params["dte_min"],
                dte_max=proxy_params["dte_max"],
                ivr_threshold=proxy_params["ivr_threshold"],
            )
            result = self._engine.run(params)
        except Exception:
            logger.exception(
                "Proxy backtest failed for %s/%s [%s→%s]",
                symbol, strategy, start_date, end_date,
            )
            return {
                "trade_count": 0, "pnl_total": None, "pnl_per_trade": None,
                "win_rate": None, "proxy_params_json": proxy_params,
            }

        trades = result.trades if result else []
        pnls = [t.pnl for t in trades if getattr(t, "pnl", None) is not None]
        trade_count = len(pnls)
        pnl_total = round(sum(pnls), 2) if pnls else None
        pnl_per_trade = round(pnl_total / trade_count, 2) if pnls else None
        wins = sum(1 for p in pnls if p > 0)
        win_rate = round(wins / trade_count, 4) if pnls else None

        return {
            "trade_count": trade_count,
            "pnl_total": pnl_total,
            "pnl_per_trade": pnl_per_trade,
            "win_rate": win_rate,
            "proxy_params_json": proxy_params,
        }
