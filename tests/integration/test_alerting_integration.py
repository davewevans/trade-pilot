"""Integration tests for the alerting layer."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestCircuitBreakerRedTransitionFiresCritical:
    def test_circuit_breaker_red_transition_fires_critical_notification(self, tmp_path):
        """Driving the CB to RED via update() fires a critical notification."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        snaps_dir = data_dir / "snapshots"
        snaps_dir.mkdir()

        from strategies.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(
            data_dir=data_dir,
            snapshots_dir=snaps_dir,
            daily_loss_halt_pct=3.0,
            daily_loss_reduce_pct=1.5,
            weekly_loss_halt_pct=5.0,
            drawdown_halt_pct=10.0,
            drawdown_lock_pct=15.0,
        )

        notified = []

        def capture_notify(severity, title, message, *, tags=None):
            notified.append({"severity": severity, "title": title, "tags": tags})

        # Patch DRY_RUN=False so the CB actually evaluates thresholds
        with patch("notifications.notify", side_effect=capture_notify), \
             patch("strategies.circuit_breaker.CircuitBreaker.update",
                   wraps=cb.update) as _wrapped, \
             patch("config.settings") as mock_cfg:
            mock_cfg.DRY_RUN = False
            cb.update(100_000)
            cb.reset_daily()
            # Drive to RED (daily loss > 3%)
            cb.update(96_000)

        critical_calls = [n for n in notified if n["severity"] == "critical"]
        assert len(critical_calls) >= 1, \
            f"Expected at least 1 critical notification. Got: {notified}"
        assert any("circuit breaker" in n["title"].lower() or "RED" in n["title"]
                   for n in critical_calls), \
            f"Expected a 'Circuit breaker RED' notification. Got: {[n['title'] for n in critical_calls]}"


class TestCircuitBreakerYellowTransitionFiresWarning:
    def test_circuit_breaker_yellow_fires_warning(self, tmp_path):
        """Driving the CB to YELLOW fires a high-priority notification."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        snaps_dir = data_dir / "snapshots"
        snaps_dir.mkdir()

        from strategies.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(
            data_dir=data_dir,
            snapshots_dir=snaps_dir,
            daily_loss_halt_pct=3.0,
            daily_loss_reduce_pct=1.5,
            weekly_loss_halt_pct=5.0,
            drawdown_halt_pct=10.0,
            drawdown_lock_pct=15.0,
        )

        notified = []

        def capture_notify(severity, title, message, *, tags=None):
            notified.append({"severity": severity, "title": title, "tags": tags})

        # Patch DRY_RUN=False so the CB actually evaluates thresholds
        with patch("notifications.notify", side_effect=capture_notify), \
             patch("config.settings") as mock_cfg:
            mock_cfg.DRY_RUN = False
            cb.update(100_000)
            cb.reset_daily()
            # Drive to YELLOW (daily loss 1.5-3%)
            cb.update(98_500)

        high_calls = [n for n in notified if n["severity"] == "high"]
        assert len(high_calls) >= 1, \
            f"Expected at least 1 high notification for YELLOW. Got: {notified}"


class TestHaltedLockFiresCritical:
    def test_halted_lock_fires_critical_notification(self, tmp_path):
        """Writing HALTED.lock fires a critical 'Trading HALTED' notification."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        snaps_dir = data_dir / "snapshots"
        snaps_dir.mkdir()

        from strategies.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(
            data_dir=data_dir,
            snapshots_dir=snaps_dir,
            daily_loss_halt_pct=3.0,
            daily_loss_reduce_pct=1.5,
            weekly_loss_halt_pct=5.0,
            drawdown_halt_pct=10.0,
            drawdown_lock_pct=15.0,
        )

        notified = []

        def capture_notify(severity, title, message, *, tags=None):
            notified.append({"severity": severity, "title": title, "tags": tags or []})

        # Patch DRY_RUN=False so the CB actually evaluates thresholds and writes the lock
        with patch("notifications.notify", side_effect=capture_notify), \
             patch("config.settings") as mock_cfg:
            mock_cfg.DRY_RUN = False
            cb.update(100_000)
            cb.update(84_000)  # triggers drawdown lock (16%)

        halt_notifications = [
            n for n in notified
            if n["severity"] == "critical" and "halt" in (n["title"] or "").lower()
        ]
        assert len(halt_notifications) >= 1, \
            f"Expected at least 1 halt notification. Got: {notified}"


class TestNotifyImportableFromMainContext:
    def test_startup_notify_importable(self):
        """notify is importable and callable — guards against import breakage."""
        from notifications import notify
        # Should be callable without error (no network call since NTFY_TOPIC unset)
        import os
        old = os.environ.pop("NTFY_TOPIC", None)
        try:
            notify("info", "test", "test message")
        finally:
            if old is not None:
                os.environ["NTFY_TOPIC"] = old


class TestDataSourceThreeConsecutiveFailuresFiresWarning:
    def test_orats_summaries_three_consecutive_failures_fires_warning(self):
        """Three consecutive ORATS /summaries failures fire a warning notification."""
        import requests

        # Reset the module-level counter between tests
        import data.orats_client as orats_mod
        orats_mod._consecutive_failures.clear()

        notified = []

        def capture_notify(severity, title, message, *, tags=None):
            notified.append({"severity": severity, "title": title, "tags": tags or []})

        from data.orats_client import ORATSClient

        client = ORATSClient(api_key="fake-key")
        # Bypass the cache for summaries
        orats_mod._cache._store = {}

        with patch("data.orats_client.requests.get") as mock_get, \
             patch("notifications.notify", side_effect=capture_notify):

            mock_get.side_effect = requests.ConnectionError("host unreachable")

            # Call 3 times (each hits the API because cache is empty per symbol)
            client.get_summary("SYM1")
            client.get_summary("SYM2")
            client.get_summary("SYM3")

        # At exactly 3 consecutive failures, a warning should fire
        warning_calls = [n for n in notified if n["severity"] == "warning"]
        assert len(warning_calls) >= 1, \
            f"Expected at least 1 warning after 3 consecutive failures. Got: {notified}"
        assert any("orats" in (n["title"] or "").lower() or "summaries" in (n["title"] or "").lower()
                   for n in warning_calls), \
            f"Expected ORATS warning. Got titles: {[n['title'] for n in warning_calls]}"
