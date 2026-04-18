"""Tests for evaluation.flag_detector.detect_flags.

Covers:
  - Absolute-threshold violation produces a flag
  - Exactly at threshold is a pass (boundary = no flag)
  - Low-sample strategy produces an insufficient_sample marker and NO regular flags
  - Trend detection fires with 3+ months of history
  - Trend check is skipped (not errored) with < 3 months of history
  - Trend without sufficient total drop does NOT flag
  - Non-monotone series does NOT flag as trend
  - Multiple strategies: low-sample and sufficient-sample handled independently
"""

import pytest

from evaluation.flag_detector import detect_flags
from evaluation.thresholds import (
    ABSOLUTE_THRESHOLDS,
    SAMPLE_SIZE_GATES,
    TREND_SENSITIVITY,
    TREND_WINDOW_MONTHS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_aggregate(
    month: str = "2026-04",
    strategy: str = "wheel",
    dimension: str = "rule_adherence",
    mean: float = 0.8,
    decisions_scored: int = 20,
    lowest_ids: list | None = None,
) -> dict:
    """Build a minimal aggregate dict for a single strategy + dimension."""
    return {
        "month": month,
        "by_strategy": {
            strategy: {
                "by_dimension": {
                    dimension: {
                        "mean": mean,
                        "median": mean,
                        "stddev": 0.05,
                        "n": decisions_scored,
                        "by_prompt_version": {},
                        "_lowest_decision_ids": lowest_ids or [101, 102, 103],
                    }
                },
                "decisions_scored": decisions_scored,
                "closed_trades_in_window": 5,
            }
        },
        "overall": {"by_dimension": {}, "decisions_scored": decisions_scored},
    }


def _make_history_aggregate(
    month: str,
    strategy: str,
    dimension: str,
    mean: float,
    decisions_scored: int = 20,
) -> dict:
    """Build a prior-month aggregate for use as history entries."""
    return _make_aggregate(month=month, strategy=strategy, dimension=dimension,
                           mean=mean, decisions_scored=decisions_scored)


def _flags_for(agg: dict, history: list | None = None) -> list[dict]:
    return detect_flags(agg, history or [])


def _regular_flags(flags: list[dict]) -> list[dict]:
    return [f for f in flags if not f.get("insufficient_sample")]


def _sample_markers(flags: list[dict]) -> list[dict]:
    return [f for f in flags if f.get("insufficient_sample")]


# ── Absolute threshold tests ──────────────────────────────────────────────────

class TestAbsoluteThreshold:

    def test_below_threshold_produces_flag(self):
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        agg = _make_aggregate(mean=threshold - 0.05)
        flags = _flags_for(agg)
        reg = _regular_flags(flags)
        assert len(reg) == 1
        assert reg[0]["strategy"] == "wheel"
        assert reg[0]["dimension"] == "rule_adherence"
        assert "severity" in reg[0]
        assert "lowest_scoring_decisions" in reg[0]

    def test_exactly_at_threshold_is_not_flagged(self):
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        agg = _make_aggregate(mean=threshold)  # exactly at boundary
        flags = _flags_for(agg)
        assert _regular_flags(flags) == []

    def test_above_threshold_no_flag(self):
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        agg = _make_aggregate(mean=threshold + 0.05)
        flags = _flags_for(agg)
        assert _regular_flags(flags) == []

    def test_flag_includes_lowest_scoring_decisions(self):
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        ids = [10, 20, 30]
        agg = _make_aggregate(mean=threshold - 0.1, lowest_ids=ids)
        flags = _flags_for(agg)
        reg = _regular_flags(flags)
        assert reg[0]["lowest_scoring_decisions"] == ids

    def test_lowest_scoring_decisions_capped_at_five(self):
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        ids = [1, 2, 3, 4, 5, 6, 7]
        agg = _make_aggregate(mean=threshold - 0.1, lowest_ids=ids)
        flags = _flags_for(agg)
        assert len(_regular_flags(flags)[0]["lowest_scoring_decisions"]) <= 5

    def test_flag_reason_mentions_threshold(self):
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        agg = _make_aggregate(mean=threshold - 0.1)
        flags = _flags_for(agg)
        reason = _regular_flags(flags)[0]["reason"]
        assert "threshold" in reason.lower() or str(round(threshold, 4)) in reason


# ── Low-sample tests ──────────────────────────────────────────────────────────

class TestLowSample:

    def test_low_sample_produces_insufficient_sample_marker(self):
        gate = SAMPLE_SIZE_GATES["wheel"]
        agg = _make_aggregate(decisions_scored=gate - 1)  # one below gate
        flags = _flags_for(agg)
        markers = _sample_markers(flags)
        assert len(markers) == 1
        assert markers[0]["strategy"] == "wheel"
        assert markers[0]["insufficient_sample"] is True
        assert markers[0]["decisions_scored"] == gate - 1
        assert markers[0]["sample_size_gate"] == gate

    def test_low_sample_produces_no_regular_flags(self):
        gate = SAMPLE_SIZE_GATES["wheel"]
        # Mean is well below threshold, but sample is too small
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        agg = _make_aggregate(mean=threshold - 0.2, decisions_scored=gate - 1)
        flags = _flags_for(agg)
        assert _regular_flags(flags) == []

    def test_exactly_at_gate_is_not_low_sample(self):
        gate = SAMPLE_SIZE_GATES["wheel"]
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        agg = _make_aggregate(mean=threshold + 0.1, decisions_scored=gate)
        flags = _flags_for(agg)
        assert _sample_markers(flags) == []

    def test_low_sample_and_sufficient_sample_strategies_independent(self):
        """One strategy below gate and one above — only the below gets a marker."""
        gate_wheel = SAMPLE_SIZE_GATES["wheel"]
        gate_condor = SAMPLE_SIZE_GATES.get("iron_condor", 5)
        threshold = ABSOLUTE_THRESHOLDS["rule_adherence"]
        agg = {
            "month": "2026-04",
            "by_strategy": {
                "wheel": {
                    "by_dimension": {
                        "rule_adherence": {
                            "mean": threshold + 0.1,
                            "median": threshold + 0.1,
                            "stddev": 0.01,
                            "n": gate_wheel,
                            "by_prompt_version": {},
                            "_lowest_decision_ids": [],
                        }
                    },
                    "decisions_scored": gate_wheel,
                    "closed_trades_in_window": 0,
                },
                "iron_condor": {
                    "by_dimension": {
                        "rule_adherence": {
                            "mean": threshold - 0.3,
                            "median": threshold - 0.3,
                            "stddev": 0.01,
                            "n": gate_condor - 1,
                            "by_prompt_version": {},
                            "_lowest_decision_ids": [],
                        }
                    },
                    "decisions_scored": gate_condor - 1,
                    "closed_trades_in_window": 0,
                },
            },
            "overall": {"by_dimension": {}, "decisions_scored": gate_wheel + gate_condor - 1},
        }

        flags = detect_flags(agg, [])
        # wheel: sufficient sample, score OK → no flags
        wheel_flags = [f for f in flags if f.get("strategy") == "wheel"]
        assert all(not f.get("insufficient_sample") for f in wheel_flags)
        # iron_condor: insufficient sample → marker, no regular flag
        condor_flags = [f for f in flags if f.get("strategy") == "iron_condor"]
        assert any(f.get("insufficient_sample") for f in condor_flags)
        assert not any("dimension" in f for f in condor_flags)


# ── Trend detection tests ─────────────────────────────────────────────────────

class TestTrendDetection:

    def _declining_history(self, n_months: int, start: float = 0.9,
                            step: float = 0.06) -> list[dict]:
        """Build n_months aggregates with a consistently declining mean."""
        history = []
        for i in range(n_months):
            mean = round(start - i * step, 4)
            month = f"2026-{i + 1:02d}"
            history.append(
                _make_history_aggregate(month, "wheel", "rule_adherence", mean=mean)
            )
        return history

    def test_trend_flag_fires_with_three_months_history(self):
        """3 months of history + declining current → trend flag."""
        # history: [0.90, 0.84, 0.78], current: 0.72 → 4 data points;
        # trend series uses last 2 from history + current = [0.84, 0.78, 0.72]
        history = self._declining_history(n_months=TREND_WINDOW_MONTHS, start=0.90, step=0.06)
        current_mean = 0.72  # below rule_adherence threshold + continues decline
        agg = _make_aggregate(mean=current_mean, decisions_scored=20)

        flags = detect_flags(agg, history)
        trend_flags = [
            f for f in flags
            if not f.get("insufficient_sample") and "declining_trend" in f.get("reason", "")
        ]
        assert len(trend_flags) >= 1
        tf = trend_flags[0]
        assert tf["strategy"] == "wheel"
        assert tf["dimension"] == "rule_adherence"

    def test_trend_check_skipped_with_fewer_than_three_months_history(self):
        """< TREND_WINDOW_MONTHS months of history → no trend flag, no error."""
        # Build 2 months of declining history (below the 3-month gate)
        history = self._declining_history(n_months=TREND_WINDOW_MONTHS - 1, start=0.90, step=0.06)
        assert len(history) < TREND_WINDOW_MONTHS  # sanity check

        current_mean = 0.72
        agg = _make_aggregate(mean=current_mean, decisions_scored=20)

        # Should not raise and should not produce a trend flag
        flags = detect_flags(agg, history)
        trend_flags = [
            f for f in flags
            if not f.get("insufficient_sample") and "declining_trend" in f.get("reason", "")
        ]
        assert trend_flags == []

    def test_trend_not_flagged_if_drop_below_sensitivity(self):
        """Monotone decline but total drop < TREND_SENSITIVITY → no trend flag."""
        tiny_step = TREND_SENSITIVITY / (TREND_WINDOW_MONTHS * 2)  # tiny per-step drop
        history = self._declining_history(
            n_months=TREND_WINDOW_MONTHS, start=0.90, step=tiny_step
        )
        current_mean = round(0.90 - TREND_WINDOW_MONTHS * tiny_step, 6)
        agg = _make_aggregate(mean=current_mean, decisions_scored=20)

        flags = detect_flags(agg, history)
        trend_flags = [
            f for f in flags
            if not f.get("insufficient_sample") and "declining_trend" in f.get("reason", "")
        ]
        assert trend_flags == []

    def test_non_monotone_series_not_flagged_as_trend(self):
        """Series that bounces (not monotone) does NOT produce a trend flag."""
        # Build history with a non-monotone pattern: down, up
        history = [
            _make_history_aggregate("2026-01", "wheel", "rule_adherence", mean=0.85),
            _make_history_aggregate("2026-02", "wheel", "rule_adherence", mean=0.75),
            _make_history_aggregate("2026-03", "wheel", "rule_adherence", mean=0.80),  # up
        ]
        current_mean = 0.70  # drop at end
        agg = _make_aggregate(mean=current_mean, decisions_scored=20)

        flags = detect_flags(agg, history)
        trend_flags = [
            f for f in flags
            if not f.get("insufficient_sample") and "declining_trend" in f.get("reason", "")
        ]
        assert trend_flags == []

    def test_trend_check_skipped_with_zero_history(self):
        """Empty history → no trend flag, no error."""
        agg = _make_aggregate(mean=0.5, decisions_scored=20)
        flags = detect_flags(agg, [])
        trend_flags = [
            f for f in flags
            if not f.get("insufficient_sample") and "declining_trend" in f.get("reason", "")
        ]
        assert trend_flags == []

    def test_trend_flag_contains_expected_fields(self):
        """Trend flag has all required fields."""
        history = self._declining_history(n_months=TREND_WINDOW_MONTHS, start=0.90, step=0.06)
        agg = _make_aggregate(mean=0.72, decisions_scored=20)

        flags = detect_flags(agg, history)
        trend_flags = [
            f for f in flags
            if not f.get("insufficient_sample") and "declining_trend" in f.get("reason", "")
        ]
        if trend_flags:
            tf = trend_flags[0]
            assert "strategy" in tf
            assert "dimension" in tf
            assert "reason" in tf
            assert "severity" in tf
            assert "lowest_scoring_decisions" in tf

    def test_missing_dimension_in_history_skips_trend_gracefully(self):
        """If a prior month lacks the dimension, trend series is incomplete → skip."""
        history = [
            _make_history_aggregate("2026-01", "wheel", "OTHER_DIM", mean=0.90),
            _make_history_aggregate("2026-02", "wheel", "OTHER_DIM", mean=0.80),
            _make_history_aggregate("2026-03", "wheel", "OTHER_DIM", mean=0.70),
        ]
        # current aggregate uses rule_adherence, history uses OTHER_DIM
        agg = _make_aggregate(mean=0.60, dimension="rule_adherence", decisions_scored=20)

        flags = detect_flags(agg, history)
        trend_flags = [
            f for f in flags
            if not f.get("insufficient_sample") and "declining_trend" in f.get("reason", "")
        ]
        assert trend_flags == []


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_aggregate_returns_empty_flags(self):
        agg = {"month": "2026-04", "by_strategy": {}, "overall": {}}
        assert detect_flags(agg, []) == []

    def test_no_flags_when_all_scores_above_threshold(self):
        for dim, threshold in ABSOLUTE_THRESHOLDS.items():
            agg = _make_aggregate(dimension=dim, mean=threshold + 0.05, decisions_scored=20)
            flags = _flags_for(agg)
            assert _regular_flags(flags) == [], f"Unexpected flag for {dim} at {threshold + 0.05}"

    def test_unknown_strategy_uses_default_gate(self):
        default_gate = SAMPLE_SIZE_GATES["_default"]
        agg = _make_aggregate(strategy="exotic_strategy", decisions_scored=default_gate - 1)
        flags = _flags_for(agg)
        markers = _sample_markers(flags)
        assert len(markers) == 1
        assert markers[0]["sample_size_gate"] == default_gate
