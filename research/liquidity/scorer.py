"""Composite liquidity scoring — pure math, no I/O.

All functions are side-effect free. The repository layer handles
persistence; this module is concerned only with computation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from research.liquidity.thresholds import (
    LIQUIDITY_FLOORS,
    METRIC_WEIGHTS,
    MIN_SNAPSHOTS_HIGH_CONFIDENCE,
    MIN_SNAPSHOTS_LOW_CONFIDENCE,
    TIER_MULTIPLIERS,
)


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiquiditySnapshot:
    symbol: str
    strategy_type: str
    snapshot_date: str
    source: Literal["cycle", "scanner"]
    avg_ba_spread_pct: float | None
    avg_oi_at_strikes: int | None
    volume_to_oi_ratio: float | None
    slippage_estimate: float | None
    sample_count: int

    def is_usable(self) -> bool:
        return (
            self.sample_count > 0
            and self.avg_ba_spread_pct is not None
            and self.avg_oi_at_strikes is not None
        )


@dataclass
class LiquidityScore:
    symbol: str
    strategy_type: str
    composite_score: float
    tier: str  # assigned by assign_tiers, placeholder 'B' from score_composite
    lookback_days: int
    snapshot_count: int
    below_floor: bool
    confidence: Literal["high", "low", "none", "disabled"]
    sub_metrics: dict[str, float] = field(default_factory=dict)


# ── Private helpers ─────────────────────────────────────────────────────────────

def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile (numpy default / method 7).

    Edge cases:
    - n=0  → 0
    - n=1  → sorted_values[0]
    - else → floor + fractional interpolation
    """
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_values[0])
    idx = q * (n - 1)
    lo = int(math.floor(idx))
    hi = lo + 1
    if hi >= n:
        return float(sorted_values[-1])
    frac = idx - lo
    return float(sorted_values[lo]) + frac * (float(sorted_values[hi]) - float(sorted_values[lo]))


def _log_percentile_rank(
    value: float,
    distribution: list[float],
    inverse: bool = False,
) -> float:
    """Return 0-100 percentile rank using a log scale.

    Log scale is appropriate because liquidity metrics span orders of
    magnitude (e.g., OI might range from 10 to 100,000).

    Args:
        value: The value to rank.
        distribution: Reference distribution (raw values, need not be sorted).
        inverse: If True, lower value = higher score (e.g., tighter spread is better).

    Edge cases:
        - Empty distribution → 50 (neutral)
        - Zero or negative value → 0 if not inverse, 100 if inverse
    """
    if not distribution:
        return 50.0

    # Zero / negative value handling
    if value <= 0:
        return 100.0 if inverse else 0.0

    # Build log-scale distribution, filtering non-positive entries
    log_dist = [math.log(v) for v in distribution if v > 0]
    if not log_dist:
        return 50.0

    log_val = math.log(value)

    n = len(log_dist)
    # Count values strictly less than log_val and values <= log_val
    lt_count = sum(1 for v in log_dist if v < log_val)
    le_count = sum(1 for v in log_dist if v <= log_val)

    # Midpoint of the [lt, le] range → handles ties
    raw_rank = (lt_count + le_count) / (2.0 * n) * 100.0

    if inverse:
        return 100.0 - raw_rank
    return raw_rank


# ── Public functions ─────────────────────────────────────────────────────────────

def aggregate_snapshots(snapshots: list[LiquiditySnapshot]) -> dict | None:
    """Average sub-metrics across usable snapshots.

    Returns a dict with keys: avg_ba_spread_pct, avg_oi_at_strikes,
    volume_to_oi_ratio (may be None if all snapshots lacked volume),
    slippage_estimate, snapshot_count.

    Returns None if there are no usable snapshots.
    """
    usable = [s for s in snapshots if s.is_usable()]
    if not usable:
        return None

    avg_spread = sum(s.avg_ba_spread_pct for s in usable) / len(usable)  # type: ignore[arg-type]
    avg_oi = int(sum(s.avg_oi_at_strikes for s in usable) / len(usable))  # type: ignore[arg-type]

    vol_oi_values = [
        s.volume_to_oi_ratio for s in usable if s.volume_to_oi_ratio is not None
    ]
    avg_vol_oi = (sum(vol_oi_values) / len(vol_oi_values)) if vol_oi_values else None

    avg_slip = (
        sum(s.slippage_estimate for s in usable if s.slippage_estimate is not None)
        / len([s for s in usable if s.slippage_estimate is not None])
        if any(s.slippage_estimate is not None for s in usable)
        else None
    )

    return {
        "avg_ba_spread_pct": avg_spread,
        "avg_oi_at_strikes": avg_oi,
        "volume_to_oi_ratio": avg_vol_oi,
        "slippage_estimate": avg_slip,
        "snapshot_count": len(usable),
    }


def extract_universe_distributions(
    aggregated_by_symbol: dict[str, dict],
) -> dict[str, list[float]]:
    """Build per-metric distributions from aggregated symbol data.

    Filters out None values and non-positive values (they can't be
    log-ranked). The result is used as the reference distribution for
    percentile ranking.
    """
    distributions: dict[str, list[float]] = {
        "avg_ba_spread_pct": [],
        "avg_oi_at_strikes": [],
        "volume_to_oi_ratio": [],
        "slippage_estimate": [],
    }
    for agg in aggregated_by_symbol.values():
        for metric in distributions:
            val = agg.get(metric)
            if val is not None and val > 0:
                distributions[metric].append(float(val))
    return distributions


def score_composite(
    symbol: str,
    strategy_type: str,
    snapshots: list[LiquiditySnapshot],
    lookback_days: int,
    universe_distributions: dict[str, list[float]],
) -> LiquidityScore | None:
    """Compute a composite liquidity score for one (symbol, strategy_type).

    Returns None if there are no usable snapshots.
    Sets tier='B' as placeholder; caller must run assign_tiers() to
    get the real tier.
    """
    agg = aggregate_snapshots(snapshots)
    if agg is None:
        return None

    n = agg["snapshot_count"]
    if n >= MIN_SNAPSHOTS_HIGH_CONFIDENCE:
        confidence: Literal["high", "low", "none", "disabled"] = "high"
    elif n >= MIN_SNAPSHOTS_LOW_CONFIDENCE:
        confidence = "low"
    else:
        confidence = "none"

    # Normalize each metric via log-percentile rank
    norm_spread = _log_percentile_rank(
        agg["avg_ba_spread_pct"] or 0,
        universe_distributions.get("avg_ba_spread_pct", []),
        inverse=True,
    )
    norm_oi = _log_percentile_rank(
        float(agg["avg_oi_at_strikes"] or 0),
        universe_distributions.get("avg_oi_at_strikes", []),
        inverse=False,
    )
    norm_vol_oi = _log_percentile_rank(
        agg["volume_to_oi_ratio"] or 0,
        universe_distributions.get("volume_to_oi_ratio", []),
        inverse=False,
    ) if agg["volume_to_oi_ratio"] is not None else 50.0  # neutral when unknown

    norm_slip = _log_percentile_rank(
        agg["slippage_estimate"] or 0,
        universe_distributions.get("slippage_estimate", []),
        inverse=True,
    ) if agg["slippage_estimate"] is not None else 50.0  # neutral when unknown

    composite = round(
        METRIC_WEIGHTS["avg_ba_spread_pct"] * norm_spread
        + METRIC_WEIGHTS["avg_oi_at_strikes"] * norm_oi
        + METRIC_WEIGHTS["volume_to_oi_ratio"] * norm_vol_oi
        + METRIC_WEIGHTS["slippage_estimate"] * norm_slip,
        2,
    )

    # Below-floor check
    floor = LIQUIDITY_FLOORS.get(strategy_type)
    below_floor = False
    if floor is not None:
        avg_oi_val = agg["avg_oi_at_strikes"] or 0
        avg_spread_val = agg["avg_ba_spread_pct"] or 0.0
        if avg_oi_val < floor.min_avg_oi or avg_spread_val > floor.max_avg_ba_spread_pct:
            below_floor = True

    sub_metrics: dict[str, float] = {
        "avg_ba_spread_pct": agg["avg_ba_spread_pct"] or 0.0,
        "avg_oi_at_strikes": float(agg["avg_oi_at_strikes"] or 0),
        "volume_to_oi_ratio": float(agg["volume_to_oi_ratio"]) if agg["volume_to_oi_ratio"] is not None else 0.0,
        "slippage_estimate": float(agg["slippage_estimate"]) if agg["slippage_estimate"] is not None else 0.0,
        "norm_ba_spread": norm_spread,
        "norm_oi": norm_oi,
        "norm_vol_oi": norm_vol_oi,
        "norm_slippage": norm_slip,
    }

    return LiquidityScore(
        symbol=symbol,
        strategy_type=strategy_type,
        composite_score=composite,
        tier="B",  # placeholder; assign_tiers fills real value
        lookback_days=lookback_days,
        snapshot_count=n,
        below_floor=below_floor,
        confidence=confidence,
        sub_metrics=sub_metrics,
    )


def assign_tiers(scores: list[LiquidityScore] | list) -> list[LiquidityScore]:
    """Assign tiers to a list of LiquidityScore objects, mutating in place.

    Rules:
    - below_floor=True → always Tier D (regardless of composite score)
    - confidence='none' → always Tier B (no signal to rank)
    - < 4 rankable scores in a strategy → default B (or D if below_floor)
    - else: top quartile A, second B, third C, bottom D
      (quartile cutoffs computed per strategy_type independently)

    Returns the same list.
    """
    scores = list(scores)

    # Group rankable scores by strategy_type
    by_strategy: dict[str, list[LiquidityScore]] = {}
    for s in scores:
        if not s.below_floor and s.confidence != "none":
            by_strategy.setdefault(s.strategy_type, []).append(s)

    # Compute quartile cutoffs per strategy
    cutoffs: dict[str, tuple[float, float, float]] = {}
    for strategy_type, rankable in by_strategy.items():
        if len(rankable) < 4:
            continue  # default B for small groups
        vals = sorted(s.composite_score for s in rankable)
        q25 = _quantile(vals, 0.25)
        q50 = _quantile(vals, 0.50)
        q75 = _quantile(vals, 0.75)
        cutoffs[strategy_type] = (q25, q50, q75)

    # Assign tiers
    for s in scores:
        if s.below_floor:
            s.tier = "D"
            continue
        if s.confidence == "none":
            s.tier = "B"
            continue
        if s.strategy_type not in cutoffs:
            s.tier = "B"
            continue
        q25, q50, q75 = cutoffs[s.strategy_type]
        if s.composite_score >= q75:
            s.tier = "A"
        elif s.composite_score >= q50:
            s.tier = "B"
        elif s.composite_score >= q25:
            s.tier = "C"
        else:
            s.tier = "D"

    return scores


def tier_to_multiplier(
    tier: str,
    confidence: str,
    multiplier_enabled: bool,
) -> float:
    """Return the score multiplier for a given tier/confidence/kill-switch state.

    Truth table:
    - kill switch off (multiplier_enabled=False) → 1.0 always
    - Tier D → 0.0 always (hard floor; confidence doesn't matter)
    - confidence in ('none', 'disabled', 'low') → 1.0 (neutral)
    - else → TIER_MULTIPLIERS[tier]
    """
    if not multiplier_enabled:
        return 1.0
    if tier == "D":
        return 0.0
    if confidence in ("none", "disabled", "low"):
        return 1.0
    return TIER_MULTIPLIERS.get(tier, 1.0)
