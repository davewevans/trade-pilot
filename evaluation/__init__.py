"""Evaluation package for programmatic and LLM-based decision scoring.

This package implements the offline rubric evaluation layer described in
knowledge/synthesized/decision_rubric.md.

RUBRIC_VERSION is the canonical version tag written to the ``rubric_version``
column of every decision_scores row produced by this package. Bump this
constant when the rubric changes in a way that invalidates old scores.
"""

RUBRIC_VERSION = "v1.0.0"
