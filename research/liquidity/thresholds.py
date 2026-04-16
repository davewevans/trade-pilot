"""Strategy-specific thresholds for liquidity scoring.

All values come directly from the Phase 1 spec
(knowledge/research_phase_1.md). They are frozen at module load time —
do not mutate at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ── Strategy types ─────────────────────────────────────────────────────────────

STRATEGY_TYPES: tuple[str, ...] = (
    "wheel_csp",
    "wheel_cc",
    "bull_put_spread",
    "bear_call_spread",
    "iron_condor",
    "long_call_vertical",
)


# ── Strike / DTE ranges ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StrikeRange:
    option_type: Literal["put", "call", "both"]
    delta_min: float
    delta_max: float
    dte_min: int
    dte_max: int


STRATEGY_STRIKE_RANGES: dict[str, StrikeRange] = {
    "wheel_csp":          StrikeRange("put",  0.20, 0.30, 21, 35),
    "wheel_cc":           StrikeRange("call", 0.20, 0.35, 21, 35),
    "bull_put_spread":    StrikeRange("put",  0.20, 0.30, 21, 35),
    "bear_call_spread":   StrikeRange("call", 0.20, 0.30, 21, 35),
    "iron_condor":        StrikeRange("both", 0.15, 0.25, 20, 50),
    "long_call_vertical": StrikeRange("call", 0.45, 0.60, 30, 60),
}


# ── Metric weights ─────────────────────────────────────────────────────────────

METRIC_WEIGHTS: dict[str, float] = {
    "avg_ba_spread_pct":  0.40,
    "avg_oi_at_strikes":  0.25,
    "volume_to_oi_ratio": 0.15,
    "slippage_estimate":  0.20,
}

assert abs(sum(METRIC_WEIGHTS.values()) - 1.0) < 1e-9, (
    f"METRIC_WEIGHTS must sum to 1.0, got {sum(METRIC_WEIGHTS.values())}"
)


# ── Tier multipliers ───────────────────────────────────────────────────────────

TIER_MULTIPLIERS: dict[str, float] = {
    "A": 1.20,
    "B": 1.00,
    "C": 0.80,
    "D": 0.00,
}


# ── Liquidity floors ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiquidityFloor:
    min_avg_oi: int
    max_avg_ba_spread_pct: float


LIQUIDITY_FLOORS: dict[str, LiquidityFloor] = {
    "wheel_csp":          LiquidityFloor(min_avg_oi=100, max_avg_ba_spread_pct=0.30),
    "wheel_cc":           LiquidityFloor(min_avg_oi=100, max_avg_ba_spread_pct=0.30),
    "bull_put_spread":    LiquidityFloor(min_avg_oi=100, max_avg_ba_spread_pct=0.25),
    "bear_call_spread":   LiquidityFloor(min_avg_oi=100, max_avg_ba_spread_pct=0.25),
    "iron_condor":        LiquidityFloor(min_avg_oi=150, max_avg_ba_spread_pct=0.20),
    "long_call_vertical": LiquidityFloor(min_avg_oi=100, max_avg_ba_spread_pct=0.30),
}


# ── Slippage factors ───────────────────────────────────────────────────────────
# Fraction of bid-ask width used to model fill cost vs. mid.
# Based on ORATS methodology: ~75% for single-leg, lower for multi-leg.

SLIPPAGE_FACTOR_BY_STRATEGY: dict[str, float] = {
    "wheel_csp":          0.75,
    "wheel_cc":           0.75,
    "bull_put_spread":    0.65,
    "bear_call_spread":   0.65,
    "iron_condor":        0.56,
    "long_call_vertical": 0.65,
}


# ── Confidence thresholds ──────────────────────────────────────────────────────

MIN_SNAPSHOTS_HIGH_CONFIDENCE: int = 30
MIN_SNAPSHOTS_LOW_CONFIDENCE: int = 5
