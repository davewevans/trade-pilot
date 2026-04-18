"""Threshold constants for the monthly evaluation pipeline.

These values are conservative starting points.  All values marked
"calibrate after first pass" should be reviewed once a full month of real
decision_scores data has been collected and the distribution of scores is
understood.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Sample-size gates
# ---------------------------------------------------------------------------
# Minimum ``decisions_scored`` required before a strategy's dimension means
# are considered reliable enough to flag.  Strategies that fall below their
# gate in a given month receive an ``insufficient_sample`` marker instead of
# flags.
#
# Calibrate after first real analysis pass.
SAMPLE_SIZE_GATES: dict[str, int] = {
    "wheel": 10,
    "conservative_wheel": 10,
    "bull_put_spread": 10,
    "bear_call_spread": 10,
    "long_call_vertical": 5,
    "iron_condor": 5,
    "iron_butterfly": 5,
    "calendar_spread": 5,
    "_default": 10,  # fallback for unknown strategy types
}

# ---------------------------------------------------------------------------
# Absolute score thresholds per dimension
# ---------------------------------------------------------------------------
# Scores are on the [0.0, 1.0] scale (matching both programmatic and
# normalised judge output).  A flag is raised when
# mean(dimension_score) < threshold for a strategy that meets its gate.
#
# Calibrate after first real analysis pass.
ABSOLUTE_THRESHOLDS: dict[str, float] = {
    # Programmatic dimensions
    "rule_adherence": 0.70,            # calibrate after first pass
    "skip_validity_structural": 0.80,  # calibrate after first pass
    # Judge dimensions (normalised from integer 1–10 to 0.0–1.0)
    "reasoning_groundedness": 0.60,    # calibrate after first pass
    "reasoning_relevance": 0.60,       # calibrate after first pass
    "reasoning_specificity": 0.60,     # calibrate after first pass
    "confidence_calibration": 0.60,    # calibrate after first pass
    "context_utilization": 0.60,       # calibrate after first pass
}

# ---------------------------------------------------------------------------
# Trend detection settings
# ---------------------------------------------------------------------------
# Number of consecutive monthly data points required to assess a trend.
# The series is built from the (TREND_WINDOW_MONTHS - 1) most recent prior
# months in history plus the current month.
TREND_WINDOW_MONTHS: int = 3

# Minimum total drop in mean score across the trend window to classify the
# series as a significant negative trend.
# Example: 0.05 means a ≥5 percentage-point cumulative decline triggers a flag.
#
# Calibrate after first real analysis pass.
TREND_SENSITIVITY: float = 0.05
