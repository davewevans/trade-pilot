"""WatchlistRecommender — Phase 3 recommendation engine.

Scores every (symbol, strategy) pair against liquidity and win-rate data
and produces add / remove / no_change recommendations for each watchlist.
Pure data generation; no watchlist.json is modified here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Watchlist → strategy types mapping
_WATCHLIST_STRATEGIES: dict[str, list[str]] = {
    "wheel": ["wheel_csp", "wheel_cc"],
    "iron_condor": ["iron_condor"],
    "iron_butterfly": ["iron_butterfly"],
    "spreads": ["bull_put_spread", "bear_call_spread", "long_call_vertical"],
    "calendar_spread": ["calendar_spread"],
}

# Win-rate tier → bonus points (added to composite score)
_WR_TIER_BONUS: dict[str, float] = {
    "strong": 30.0,
    "good": 20.0,
    "neutral": 10.0,
}

# Incumbent bonus — current members are not removed lightly
_INCUMBENT_BONUS = 15.0

# Composite score below this threshold is considered "low" for removal gate
_REMOVE_SCORE_THRESHOLD = 40.0


class WatchlistRecommender:
    """Generate add/remove/no_change recommendations for each watchlist.

    Args:
        liquidity_repo: LiquidityRepository instance.
        backtest_stats_repo: BacktestStatsRepository instance.
        universe: CandidateUniverse (or any object with an `all_symbols()` method).
        candidate_universe: Same or different universe to score candidates from.
        settings_obj: Config settings object; defaults to ``config.settings``.
    """

    def __init__(
        self,
        liquidity_repo,
        backtest_stats_repo,
        universe,
        candidate_universe,
        settings_obj=None,
        trade_repo=None,
    ):
        self._liq_repo = liquidity_repo
        self._bt_repo = backtest_stats_repo
        self._universe = universe
        self._candidate_universe = candidate_universe
        self._trade_repo = trade_repo

        if settings_obj is None:
            from config import settings
            self._settings = settings
        else:
            self._settings = settings_obj

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_live_performance_penalty(
        self,
        symbol: str,
        strategy_type: str,
    ) -> tuple[float, dict]:
        """Compute a live-performance penalty for an incumbent.

        Returns (penalty_points, sub_scores_dict).
        penalty_points is 0 or negative.
        sub_scores_dict contains: live_trade_count, live_win_rate,
        live_avg_pnl_per_trade, live_penalty_applied.
        """
        from datetime import date, timedelta
        from database.repositories.trades import trade_pnl

        LOOKBACK_DAYS = 90
        MIN_SAMPLE = 10

        sub = {
            "live_trade_count": 0,
            "live_win_rate": None,
            "live_avg_pnl_per_trade": None,
            "live_penalty_applied": 0.0,
        }

        if self._trade_repo is None:
            return 0.0, sub

        since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        try:
            trades = self._trade_repo.get_closed_trades_for_symbol(
                symbol, strategy_type, since
            )
        except Exception:
            logger.warning(
                "get_closed_trades_for_symbol failed for %s/%s", symbol, strategy_type
            )
            return 0.0, sub

        pnl_list = [trade_pnl(t) for t in trades]
        pnl_list = [p for p in pnl_list if p is not None]

        sub["live_trade_count"] = len(pnl_list)

        if len(pnl_list) < MIN_SAMPLE:
            return 0.0, sub

        wins = sum(1 for p in pnl_list if p > 0)
        win_rate = wins / len(pnl_list)
        avg_pnl = sum(pnl_list) / len(pnl_list)

        sub["live_win_rate"] = round(win_rate, 4)
        sub["live_avg_pnl_per_trade"] = round(avg_pnl, 2)

        if win_rate < 0.30:
            penalty = -25.0
        elif win_rate < 0.40:
            penalty = -15.0
        elif avg_pnl < 0:
            penalty = -10.0
        else:
            penalty = 0.0

        sub["live_penalty_applied"] = penalty
        return penalty, sub

    def _get_liq_score(self, symbol: str, strategy_type: str) -> Optional[dict]:
        """Return the liquidity score row, or None."""
        try:
            return self._liq_repo.get_score(symbol, strategy_type)
        except Exception:
            logger.debug("get_score failed for %s/%s", symbol, strategy_type)
            return None

    def _get_wr_data(self, symbol: str, strategy_type: str) -> tuple[float, str, str]:
        """Return (multiplier, tier_label, confidence) from backtest stats."""
        try:
            return self._bt_repo.get_winrate_multiplier(symbol, strategy_type)
        except Exception:
            return (1.0, "neutral", "none")

    def _weeks_observed(self, symbol: str, strategy_type: str) -> int:
        """Count distinct snapshot weeks for (symbol, strategy_type).

        Uses snapshot_date field; each weekly scan produces one distinct date.
        """
        try:
            snapshots = self._liq_repo.get_snapshots(
                symbol, strategy_type, since_date="2000-01-01"
            )
            distinct_dates = {s["snapshot_date"] for s in snapshots}
            return len(distinct_dates)
        except Exception:
            return 0

    def _score_candidate(
        self,
        symbol: str,
        strategy_type: str,
        all_watchlist_members: set[str],
        is_incumbent: bool = False,
    ) -> tuple[float, dict, str]:
        """Score a single (symbol, strategy_type) pair.

        Returns (composite_score, sub_scores_dict, data_confidence).

        composite = liq_score * 0.6 + winrate_bonus + novelty_bonus

        data_confidence:
          'high'  — both liquidity and winrate confidence are 'high'
          'low'   — either is 'low' (but neither is 'none')
          'none'  — either is 'none' (insufficient data)
        """
        liq_row = self._get_liq_score(symbol, strategy_type)
        wr_mult, wr_tier, wr_conf = self._get_wr_data(symbol, strategy_type)

        # Liquidity sub-score
        if liq_row is not None:
            liq_score = float(liq_row.get("composite_score") or 0.0)
            liq_tier = liq_row.get("tier", "D")
            liq_conf = liq_row.get("confidence", "none")
        else:
            liq_score = 0.0
            liq_tier = "D"
            liq_conf = "none"

        # Win-rate bonus
        winrate_bonus = _WR_TIER_BONUS.get(wr_tier, 0.0)

        # Novelty bonus — symbol not already in *any* watchlist
        novelty = 10.0 if symbol not in all_watchlist_members else 0.0

        composite = round(liq_score * 0.6 + winrate_bonus + novelty, 3)

        if is_incumbent:
            penalty, live_sub = self._get_live_performance_penalty(symbol, strategy_type)
            composite = round(composite + penalty, 3)
        else:
            live_sub = {}

        sub_scores = {
            "liq_score": liq_score,
            "liq_tier": liq_tier,
            "liq_confidence": liq_conf,
            "wr_multiplier": wr_mult,
            "wr_tier": wr_tier,
            "wr_confidence": wr_conf,
            "winrate_bonus": winrate_bonus,
            "novelty_bonus": novelty,
        }
        if live_sub:
            sub_scores.update(live_sub)

        # Determine data confidence
        conf_rank = {"none": 0, "low": 1, "high": 2}
        liq_rank = conf_rank.get(liq_conf, 0)
        wr_rank = conf_rank.get(wr_conf, 0)
        min_rank = min(liq_rank, wr_rank)
        if min_rank == 2:
            data_confidence = "high"
        elif min_rank == 1:
            data_confidence = "low"
        else:
            data_confidence = "none"

        return composite, sub_scores, data_confidence

    def _score_current_member(
        self,
        symbol: str,
        strategy_type: str,
        all_watchlist_members: set[str],
    ) -> tuple[float, dict]:
        """Score an existing watchlist member, applying the incumbent bonus.

        Returns (composite_score, sub_scores_dict).
        """
        composite, sub_scores, _ = self._score_candidate(
            symbol, strategy_type, all_watchlist_members
        )
        composite_with_bonus = round(composite + _INCUMBENT_BONUS, 3)
        sub_scores = {**sub_scores, "incumbent_bonus": _INCUMBENT_BONUS}
        return composite_with_bonus, sub_scores

    def _generate_reasoning(
        self,
        symbol: str,
        sub_scores: dict,
        action: str,
        weeks_observed: int = 0,
    ) -> str:
        """Build a short human-readable justification string."""
        liq_tier = sub_scores.get("liq_tier", "?")
        liq_conf = sub_scores.get("liq_confidence", "none")
        wr_tier = sub_scores.get("wr_tier", "neutral")
        wr_mult = sub_scores.get("wr_multiplier", 1.0)
        novelty = sub_scores.get("novelty_bonus", 0.0)

        parts: list[str] = []

        # Liquidity description
        if liq_conf == "none":
            parts.append("no liquidity data")
        elif liq_conf == "low":
            parts.append(f"Tier {liq_tier} liquidity (low confidence)")
        else:
            parts.append(f"Tier {liq_tier} liquidity")

        # Win-rate description
        if wr_tier == "strong":
            parts.append(f"strong win rate ({wr_mult:.2f}× multiplier)")
        elif wr_tier == "good":
            parts.append(f"good win rate ({wr_mult:.2f}× multiplier)")
        elif wr_tier == "neutral":
            parts.append("neutral win rate")
        elif wr_tier in ("weak", "poor"):
            parts.append(f"weak win rate ({wr_mult:.2f}× multiplier)")
        elif wr_tier == "reject":
            parts.append("win rate below 30% floor")
        else:
            parts.append("no win-rate data")

        # Action-specific context
        if action == "add" and novelty > 0:
            parts.append("not currently in watchlist")
        elif action == "remove":
            if weeks_observed > 0:
                parts.append(f"observed {weeks_observed} weeks")
            parts.append("score below removal threshold")
        elif action == "no_change":
            parts.append("incumbent score acceptable")

        return ", ".join(parts)

    # ── Main methods ──────────────────────────────────────────────────────────

    def generate_for_watchlist(
        self,
        watchlist_name: str,
        current_members: list[str],
        strategy_types: list[str],
    ) -> dict:
        """Generate recommendations for one watchlist.

        Returns a dict with keys: watchlist_name, add, remove, no_change,
        considered_but_rejected.
        """
        max_recs = self._settings.RESEARCH_MAX_RECOMMENDATIONS_PER_LIST
        min_weeks = self._settings.RESEARCH_REMOVE_MIN_WEEKS_OBSERVED

        # All symbols currently in any watchlist (for novelty calculation)
        all_members = set(current_members)  # at minimum, current list

        # Get all candidate symbols from universe
        try:
            all_candidates = set(self._candidate_universe.all_symbols())
        except Exception:
            logger.warning("Could not load candidate universe; using empty set")
            all_candidates = set()

        # Symbols not in this watchlist (candidates to add)
        add_pool = all_candidates - set(current_members)

        # ── Score current members ──────────────────────────────────────────

        _conf_rank = {"none": 0, "low": 1, "high": 2}
        _rank_conf = {0: "none", 1: "low", 2: "high"}

        member_scores: list[dict] = []
        for sym in current_members:
            sym = sym.upper()
            # Average score across all strategy_types for this watchlist
            scores = []
            combined_sub: dict[str, Any] = {}
            conf_ranks: list[int] = []
            for strat in strategy_types:
                score, sub, data_conf = self._score_candidate(sym, strat, all_members, is_incumbent=True)
                scores.append(score)
                # Merge sub_scores (last strategy wins for shared keys; good enough)
                combined_sub.update(sub)
                conf_ranks.append(_conf_rank.get(data_conf, 0))

            avg_score = sum(scores) / len(scores) if scores else 0.0
            member_bonus = round(avg_score + _INCUMBENT_BONUS, 3)
            combined_sub["incumbent_bonus"] = _INCUMBENT_BONUS

            # Aggregate confidence: use minimum across strategies
            agg_confidence = _rank_conf[min(conf_ranks)] if conf_ranks else "none"

            # Weeks observed (use first strategy type as proxy)
            weeks = self._weeks_observed(sym, strategy_types[0]) if strategy_types else 0

            member_scores.append({
                "symbol": sym,
                "score": member_bonus,
                "sub_scores": combined_sub,
                "weeks_observed": weeks,
                "data_confidence": agg_confidence,
            })

        # ── Score add candidates ───────────────────────────────────────────

        candidate_scores: list[dict] = []
        for sym in sorted(add_pool):
            sym = sym.upper()

            # Score across strategy_types, take average
            scores = []
            combined_sub: dict[str, Any] = {}
            any_data_conf = "none"
            for strat in strategy_types:
                score, sub, conf = self._score_candidate(sym, strat, all_members)
                scores.append(score)
                combined_sub.update(sub)
                # Track best confidence across strategies
                rank = {"none": 0, "low": 1, "high": 2}
                if rank.get(conf, 0) > rank.get(any_data_conf, 0):
                    any_data_conf = conf

            avg_score = sum(scores) / len(scores) if scores else 0.0

            candidate_scores.append({
                "symbol": sym,
                "score": round(avg_score, 3),
                "sub_scores": combined_sub,
                "data_confidence": any_data_conf,
            })

        # Sort candidates by score descending
        candidate_scores.sort(key=lambda x: x["score"], reverse=True)

        # ── Build ADD list (with data-sufficiency gates) ───────────────────

        add_list: list[dict] = []
        considered_but_rejected: list[dict] = []

        for cand in candidate_scores:
            sub = cand["sub_scores"]
            liq_tier = sub.get("liq_tier", "D")
            liq_conf = sub.get("liq_confidence", "none")
            wr_conf = sub.get("wr_confidence", "none")
            wr_mult = sub.get("wr_multiplier", 1.0)

            # Gate 1: liquidity must be high-confidence
            if liq_conf != "high":
                continue
            # Gate 2: win-rate must have at least low-confidence data
            if wr_conf == "none":
                continue
            # Gate 3: tier B or better only
            if liq_tier not in ("A", "B"):
                continue
            # Gate 4: win-rate multiplier ≥ 1.0 (neutral, good, or strong)
            if wr_mult < 1.0:
                continue

            reasoning = self._generate_reasoning(
                cand["symbol"], sub, "add"
            )

            rec = {
                "symbol": cand["symbol"],
                "score": cand["score"],
                "reasoning": reasoning,
                "data_confidence": cand["data_confidence"],
                "sub_scores": sub,
            }

            if len(add_list) < max_recs:
                add_list.append(rec)
            elif len(add_list) + len(considered_but_rejected) < max_recs + 10:
                considered_but_rejected.append(rec)

        # ── Build REMOVE / NO_CHANGE lists ────────────────────────────────

        remove_list: list[dict] = []
        no_change_list: list[dict] = []

        for m in member_scores:
            sym = m["symbol"]
            sub = m["sub_scores"]
            weeks = m["weeks_observed"]
            score = m["score"]

            # Remove gate: need minimum weeks of observed data
            can_remove = weeks >= min_weeks

            # Removal candidate: score below threshold (before incumbent bonus)
            raw_score = score - _INCUMBENT_BONUS  # strip the bonus to get base
            is_poor = raw_score < _REMOVE_SCORE_THRESHOLD

            if can_remove and is_poor and len(remove_list) < max_recs:
                reasoning = self._generate_reasoning(
                    sym, sub, "remove", weeks_observed=weeks
                )
                remove_list.append({
                    "symbol": sym,
                    "score": score,
                    "reasoning": reasoning,
                    "data_confidence": "high" if sub.get("liq_confidence") == "high" else "low",
                    "sub_scores": sub,
                })
            else:
                reasoning = self._generate_reasoning(sym, sub, "no_change")
                no_change_list.append({
                    "symbol": sym,
                    "score": score,
                    "reasoning": reasoning,
                    "data_confidence": m.get("data_confidence", "none"),
                    "sub_scores": sub,
                })

        return {
            "watchlist_name": watchlist_name,
            "add": add_list,
            "remove": remove_list,
            "no_change": no_change_list,
            "considered_but_rejected": considered_but_rejected,
        }

    def generate_all(self) -> dict:
        """Generate recommendations for all three watchlists.

        Reads current_members from data/watchlist.json.

        Returns dict with generated_at, wheel, iron_condor, spreads.
        """
        watchlist_path = self._settings.DATA_DIR / "watchlist.json"
        if watchlist_path.exists():
            try:
                watchlist_data = json.loads(
                    watchlist_path.read_text(encoding="utf-8")
                )
            except Exception:
                logger.warning("Could not read watchlist.json; using empty members")
                watchlist_data = {}
        else:
            logger.warning("watchlist.json not found at %s", watchlist_path)
            watchlist_data = {}

        results: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        for wl_name, strategy_types in _WATCHLIST_STRATEGIES.items():
            current_members = [
                s.upper() for s in watchlist_data.get(wl_name, [])
            ]
            logger.info(
                "Generating recommendations for watchlist=%s (members=%d, strategies=%s)",
                wl_name, len(current_members), strategy_types,
            )
            try:
                rec = self.generate_for_watchlist(
                    watchlist_name=wl_name,
                    current_members=current_members,
                    strategy_types=strategy_types,
                )
                results[wl_name] = rec
            except Exception:
                logger.exception(
                    "generate_for_watchlist failed for %s — using empty result",
                    wl_name,
                )
                results[wl_name] = {
                    "watchlist_name": wl_name,
                    "add": [],
                    "remove": [],
                    "no_change": [],
                    "considered_but_rejected": [],
                }

        return results

    def persist(self, results: dict, repo) -> None:
        """Write results to DB (watchlist_recommendations) and JSON snapshot.

        Args:
            results: Output of generate_all().
            repo: RecommendationRepository instance.
        """
        generated_at = results.get(
            "generated_at",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

        rows: list[dict] = []
        for wl_name in ("wheel", "iron_condor", "spreads"):
            wl_data = results.get(wl_name)
            if not wl_data:
                continue

            for action in ("add", "remove", "no_change"):
                for item in wl_data.get(action, []):
                    rows.append({
                        "generated_at": generated_at,
                        "watchlist_name": wl_name,
                        "symbol": item["symbol"],
                        "action": action,
                        "score": item["score"],
                        "reasoning": item.get("reasoning", ""),
                        "data_confidence": item.get("data_confidence", "low"),
                        "sub_scores": item.get("sub_scores"),
                    })

            # considered_but_rejected stored with action='considered'
            for item in wl_data.get("considered_but_rejected", []):
                rows.append({
                    "generated_at": generated_at,
                    "watchlist_name": wl_name,
                    "symbol": item["symbol"],
                    "action": "considered",
                    "score": item["score"],
                    "reasoning": item.get("reasoning", ""),
                    "data_confidence": item.get("data_confidence", "low"),
                    "sub_scores": item.get("sub_scores"),
                })

        inserted_ids = repo.insert_batch(rows)
        logger.info(
            "Persisted %d recommendation rows (generated_at=%s)",
            len(inserted_ids), generated_at,
        )

        # Stamp recommendation_id back onto each item so the snapshot carries it
        id_iter = iter(inserted_ids)
        for wl_name in ("wheel", "iron_condor", "spreads"):
            wl_data = results.get(wl_name)
            if not wl_data:
                continue
            for action_key in ("add", "remove", "no_change"):
                for item in wl_data.get(action_key, []):
                    item["recommendation_id"] = next(id_iter)
            for item in wl_data.get("considered_but_rejected", []):
                item["recommendation_id"] = next(id_iter)

        # Write JSON snapshot for API consumption
        snapshot_path = self._settings.SNAPSHOTS_DIR / "watchlist_recommendations.json"
        try:
            snapshot_path.write_text(
                json.dumps(results, indent=2, default=str), encoding="utf-8"
            )
            logger.info("Wrote watchlist_recommendations.json to %s", snapshot_path)
        except Exception:
            logger.warning("Could not write watchlist_recommendations.json", exc_info=True)
