"""Tests for scheduler.py — safe_run and drift halt integration."""

from unittest.mock import MagicMock, patch

import pytest


def test_safe_run_skips_job_when_drift_halt_fires():
    """safe_run does not call job_fn when check_and_halt_on_drift returns False."""
    from scheduler import safe_run

    job_fn = MagicMock()
    with patch("scheduler.check_and_halt_on_drift", return_value=False):
        safe_run(job_fn, "market_open")

    job_fn.assert_not_called()


def test_safe_run_calls_job_when_drift_check_passes():
    """safe_run calls job_fn normally when drift check returns True."""
    from scheduler import safe_run

    job_fn = MagicMock()
    with patch("scheduler.check_and_halt_on_drift", return_value=True):
        safe_run(job_fn, "pre_market")

    job_fn.assert_called_once()
