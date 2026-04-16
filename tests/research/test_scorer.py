"""Tests for research/liquidity/scorer.py"""

import pytest

from research.liquidity.scorer import (
    LiquidityScore,
    LiquiditySnapshot,
    _log_percentile_rank,
    _quantile,
    aggregate_snapshots,
    assign_tiers,
    score_composite,
    tier_to_multiplier,
)
from research.liquidity.thresholds import METRIC_WEIGHTS


# ── import-time assertion ─────────────────────────────────────────────────────

def test_metric_weights_sum_to_one():
    assert abs(sum(METRIC_WEIGHTS.values()) - 1.0) < 1e-9


# ── _quantile ─────────────────────────────────────────────────────────────────

def test_quantile_basic():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _quantile(vals, 0.50) == pytest.approx(3.0)
    assert _quantile(vals, 0.25) == pytest.approx(2.0)
    assert _quantile(vals, 0.75) == pytest.approx(4.0)


def test_quantile_empty():
    assert _quantile([], 0.5) == 0.0


def test_quantile_single():
    assert _quantile([42.0], 0.5) == 42.0


def test_quantile_q0_and_q1():
    vals = [1.0, 2.0, 3.0]
    assert _quantile(vals, 0.0) == 1.0
    assert _quantile(vals, 1.0) == 3.0


# ── _log_percentile_rank ──────────────────────────────────────────────────────

def test_log_percentile_rank_value_higher_than_all():
    dist = [1.0, 2.0, 3.0, 4.0, 5.0]
    rank = _log_percentile_rank(100.0, dist, inverse=False)
    assert rank == pytest.approx(100.0)


def test_log_percentile_rank_inverse_value_higher_than_all():
    dist = [1.0, 2.0, 3.0]
    rank = _log_percentile_rank(100.0, dist, inverse=True)
    assert rank == pytest.approx(0.0)


def test_log_percentile_rank_empty_distribution():
    assert _log_percentile_rank(5.0, [], inverse=False) == 50.0
    assert _log_percentile_rank(5.0, [], inverse=True) == 50.0


def test_log_percentile_rank_zero_value():
    dist = [1.0, 2.0, 3.0]
    assert _log_percentile_rank(0.0, dist, inverse=False) == 0.0
    assert _log_percentile_rank(0.0, dist, inverse=True) == 100.0


def test_log_percentile_rank_negative_value():
    dist = [1.0, 2.0]
    assert _log_percentile_rank(-1.0, dist, inverse=False) == 0.0
    assert _log_percentile_rank(-1.0, dist, inverse=True) == 100.0


def test_log_percentile_rank_value_lower_than_all():
    dist = [10.0, 20.0, 30.0]
    rank = _log_percentile_rank(0.001, dist, inverse=False)
    assert rank == pytest.approx(0.0)


# ── aggregate_snapshots ───────────────────────────────────────────────────────

def _make_snapshot(
    symbol="AAPL",
    strategy_type="bull_put_spread",
    snapshot_date="2026-01-01",
    source="cycle",
    avg_ba_spread_pct=0.05,
    avg_oi_at_strikes=200,
    volume_to_oi_ratio=0.10,
    slippage_estimate=0.03,
    sample_count=5,
) -> LiquiditySnapshot:
    return LiquiditySnapshot(
        symbol=symbol,
        strategy_type=strategy_type,
        snapshot_date=snapshot_date,
        source=source,
        avg_ba_spread_pct=avg_ba_spread_pct,
        avg_oi_at_strikes=avg_oi_at_strikes,
        volume_to_oi_ratio=volume_to_oi_ratio,
        slippage_estimate=slippage_estimate,
        sample_count=sample_count,
    )


def test_aggregate_mix_usable_unusable():
    usable = _make_snapshot(avg_ba_spread_pct=0.10, avg_oi_at_strikes=100, sample_count=5)
    unusable = _make_snapshot(avg_ba_spread_pct=None, avg_oi_at_strikes=None, sample_count=0)
    result = aggregate_snapshots([usable, unusable])
    assert result is not None
    assert result["snapshot_count"] == 1
    assert result["avg_ba_spread_pct"] == pytest.approx(0.10)
    assert result["avg_oi_at_strikes"] == 100


def test_aggregate_all_unusable_returns_none():
    snaps = [
        _make_snapshot(avg_ba_spread_pct=None, avg_oi_at_strikes=None, sample_count=0),
        _make_snapshot(avg_ba_spread_pct=None, avg_oi_at_strikes=50, sample_count=0),
    ]
    assert aggregate_snapshots(snaps) is None


def test_aggregate_averages_correctly():
    s1 = _make_snapshot(avg_ba_spread_pct=0.10, avg_oi_at_strikes=100, sample_count=5)
    s2 = _make_snapshot(avg_ba_spread_pct=0.20, avg_oi_at_strikes=200, sample_count=5)
    result = aggregate_snapshots([s1, s2])
    assert result is not None
    assert result["avg_ba_spread_pct"] == pytest.approx(0.15)
    assert result["avg_oi_at_strikes"] == 150
    assert result["snapshot_count"] == 2


# ── score_composite ───────────────────────────────────────────────────────────

def _make_snapshots(n: int, **kwargs) -> list[LiquiditySnapshot]:
    return [
        _make_snapshot(snapshot_date=f"2026-01-{i+1:02d}", **kwargs)
        for i in range(n)
    ]


def _simple_distributions() -> dict:
    return {
        "avg_ba_spread_pct": [0.01, 0.05, 0.10, 0.20, 0.30],
        "avg_oi_at_strikes": [50.0, 100.0, 200.0, 500.0, 1000.0],
        "volume_to_oi_ratio": [0.01, 0.05, 0.10, 0.20, 0.50],
        "slippage_estimate": [0.01, 0.03, 0.05, 0.10, 0.20],
    }


def test_score_composite_high_confidence():
    snaps = _make_snapshots(40, avg_ba_spread_pct=0.05, avg_oi_at_strikes=200,
                            volume_to_oi_ratio=0.10, slippage_estimate=0.03)
    result = score_composite("AAPL", "bull_put_spread", snaps, 30, _simple_distributions())
    assert result is not None
    assert result.confidence == "high"
    assert 0.0 <= result.composite_score <= 100.0


def test_score_composite_low_confidence():
    snaps = _make_snapshots(10, avg_ba_spread_pct=0.05, avg_oi_at_strikes=200)
    result = score_composite("AAPL", "bull_put_spread", snaps, 30, _simple_distributions())
    assert result is not None
    assert result.confidence == "low"


def test_score_composite_none_confidence():
    snaps = _make_snapshots(2, avg_ba_spread_pct=0.05, avg_oi_at_strikes=200)
    result = score_composite("AAPL", "bull_put_spread", snaps, 30, _simple_distributions())
    assert result is not None
    assert result.confidence == "none"


def test_score_composite_no_usable_snapshots_returns_none():
    snaps = _make_snapshots(5, avg_ba_spread_pct=None, avg_oi_at_strikes=None, sample_count=0)
    result = score_composite("AAPL", "bull_put_spread", snaps, 30, _simple_distributions())
    assert result is None


def test_score_composite_below_floor_flag():
    # avg_oi_at_strikes=50 < floor min_avg_oi=100 for bull_put_spread
    snaps = _make_snapshots(40, avg_ba_spread_pct=0.05, avg_oi_at_strikes=50)
    result = score_composite("AAPL", "bull_put_spread", snaps, 30, _simple_distributions())
    assert result is not None
    assert result.below_floor is True


def test_score_composite_above_floor_not_flagged():
    snaps = _make_snapshots(40, avg_ba_spread_pct=0.05, avg_oi_at_strikes=200)
    result = score_composite("AAPL", "bull_put_spread", snaps, 30, _simple_distributions())
    assert result is not None
    assert result.below_floor is False


def test_score_composite_placeholder_tier():
    snaps = _make_snapshots(10, avg_ba_spread_pct=0.05, avg_oi_at_strikes=200)
    result = score_composite("AAPL", "bull_put_spread", snaps, 30, _simple_distributions())
    assert result is not None
    assert result.tier == "B"


def test_score_composite_sub_metrics_populated():
    snaps = _make_snapshots(10, avg_ba_spread_pct=0.05, avg_oi_at_strikes=200)
    result = score_composite("AAPL", "bull_put_spread", snaps, 30, _simple_distributions())
    assert result is not None
    for key in ("norm_ba_spread", "norm_oi", "norm_vol_oi", "norm_slippage"):
        assert key in result.sub_metrics


# ── assign_tiers ──────────────────────────────────────────────────────────────

def _make_score(
    symbol: str,
    strategy_type: str,
    composite_score: float,
    below_floor: bool = False,
    confidence: str = "high",
) -> LiquidityScore:
    return LiquidityScore(
        symbol=symbol,
        strategy_type=strategy_type,
        composite_score=composite_score,
        tier="B",
        lookback_days=30,
        snapshot_count=30,
        below_floor=below_floor,
        confidence=confidence,
    )


def test_assign_tiers_per_strategy_independent():
    """Two strategies each get their own quartile split."""
    strategy_a_scores = [
        _make_score(f"SYM{i}", "bull_put_spread", float(i * 10))
        for i in range(1, 9)
    ]  # 10, 20, ..., 80
    strategy_b_scores = [
        _make_score(f"SYM{i}", "bear_call_spread", float(i * 5))
        for i in range(1, 9)
    ]  # 5, 10, ..., 40

    all_scores = strategy_a_scores + strategy_b_scores
    assign_tiers(all_scores)

    # Each strategy must have exactly one A
    a_tiers = [s.tier for s in strategy_a_scores]
    b_tiers = [s.tier for s in strategy_b_scores]
    assert a_tiers.count("A") >= 1
    assert b_tiers.count("A") >= 1

    # The top score in each strategy should be A
    top_a = max(strategy_a_scores, key=lambda s: s.composite_score)
    top_b = max(strategy_b_scores, key=lambda s: s.composite_score)
    assert top_a.tier == "A"
    assert top_b.tier == "A"


def test_assign_tiers_below_floor_is_d():
    s = _make_score("HOOD", "bull_put_spread", 99.0, below_floor=True)
    assign_tiers([s])
    assert s.tier == "D"


def test_assign_tiers_confidence_none_is_b():
    s = _make_score("HOOD", "bull_put_spread", 99.0, confidence="none")
    assign_tiers([s])
    assert s.tier == "B"


def test_assign_tiers_only_3_scores_default_b():
    scores = [_make_score(f"S{i}", "bull_put_spread", float(i * 10)) for i in range(1, 4)]
    assign_tiers(scores)
    for s in scores:
        assert s.tier == "B"


def test_assign_tiers_quartile_ordering():
    """With 8 hand-crafted values, top 2 (>=Q75) are A, bottom 2 (<Q25) are D."""
    # 10, 20, 30, 40, 50, 60, 70, 80 → Q25=27.5, Q50=45.0, Q75=62.5
    scores = [_make_score(f"S{i}", "bull_put_spread", float(v))
              for i, v in enumerate([10, 20, 30, 40, 50, 60, 70, 80])]
    assign_tiers(scores)
    tiers = {s.composite_score: s.tier for s in scores}
    assert tiers[80.0] == "A"
    assert tiers[70.0] == "A"
    assert tiers[10.0] == "D"
    assert tiers[20.0] == "D"


# ── tier_to_multiplier ────────────────────────────────────────────────────────

@pytest.mark.parametrize("tier,confidence,enabled,expected", [
    ("A", "high",     True,  1.20),
    ("B", "high",     True,  1.00),
    ("C", "high",     True,  0.80),
    ("D", "high",     True,  0.00),
    ("A", "high",     False, 1.00),  # kill switch off
    ("A", "low",      True,  1.00),  # low confidence → neutral
    ("A", "none",     True,  1.00),  # no confidence → neutral
    ("D", "low",      True,  0.00),  # Tier D regardless of confidence
    ("D", "none",     True,  0.00),  # Tier D regardless of confidence
])
def test_tier_to_multiplier_truth_table(tier, confidence, enabled, expected):
    assert tier_to_multiplier(tier, confidence, enabled) == pytest.approx(expected)
