"""Tests for strategies.circuit_breaker.CircuitBreaker."""

import json

import pytest

from strategies.circuit_breaker import CircuitBreaker, CircuitBreakerStatus


@pytest.fixture
def dirs(tmp_path):
    """Return (data_dir, snapshots_dir) rooted in a temp directory."""
    data = tmp_path / "data"
    snaps = data / "snapshots"
    return data, snaps


@pytest.fixture
def cb(dirs):
    """A CircuitBreaker wired to temp directories with default thresholds."""
    data_dir, snap_dir = dirs
    return CircuitBreaker(
        data_dir=data_dir,
        snapshots_dir=snap_dir,
        daily_loss_halt_pct=3.0,
        daily_loss_reduce_pct=1.5,
        weekly_loss_halt_pct=5.0,
        drawdown_halt_pct=10.0,
        drawdown_lock_pct=15.0,
    )


# ================================================================
# Threshold evaluation
# ================================================================


class TestGreen:
    def test_no_change_is_green(self, cb):
        status = cb.update(100_000)
        assert status.status == "GREEN"
        assert status.active_rules == []
        assert status.halted is False

    def test_small_gain_is_green(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        status = cb.update(100_500)
        assert status.status == "GREEN"
        assert status.daily_pnl_pct > 0


class TestDailyLossReduce:
    def test_triggers_at_threshold(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        # Down 1.5%
        status = cb.update(98_500)
        assert "daily_loss_reduce" in status.active_rules
        assert status.status == "YELLOW"

    def test_just_below_threshold_is_green(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        # Down 1.49%
        status = cb.update(98_510)
        assert status.status == "GREEN"
        assert status.active_rules == []


class TestDailyLossHalt:
    def test_triggers_at_threshold(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        # Down 3%
        status = cb.update(97_000)
        assert "daily_loss_halt" in status.active_rules
        assert status.status == "RED"

    def test_between_reduce_and_halt(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        # Down 2% — past reduce but not halt
        status = cb.update(98_000)
        assert "daily_loss_reduce" in status.active_rules
        assert "daily_loss_halt" not in status.active_rules
        assert status.status == "YELLOW"


class TestWeeklyLossHalt:
    def test_triggers_at_threshold(self, cb):
        cb.update(100_000)
        cb.reset_weekly()
        # Down 5%
        status = cb.update(95_000)
        assert "weekly_loss_halt" in status.active_rules
        assert status.status == "RED"

    def test_below_threshold_no_rule(self, cb):
        cb.update(100_000)
        cb.reset_weekly()
        status = cb.update(95_100)
        assert "weekly_loss_halt" not in status.active_rules


class TestDrawdownHalt:
    def test_triggers_at_threshold(self, cb):
        # Set peak high, then drop 10%
        cb.update(100_000)
        status = cb.update(90_000)
        assert "drawdown_halt" in status.active_rules
        assert status.status == "RED"

    def test_peak_tracks_rolling_max(self, cb):
        cb.update(100_000)
        cb.update(110_000)  # new peak
        # 10% off the new peak
        status = cb.update(99_000)
        assert "drawdown_halt" in status.active_rules
        assert cb.peak_equity == 110_000


class TestDrawdownLock:
    def test_writes_lock_file(self, dirs, cb):
        data_dir, _ = dirs
        cb.update(100_000)
        status = cb.update(85_000)  # down 15%
        assert "drawdown_lock" in status.active_rules
        lock = data_dir / "HALTED.lock"
        assert lock.exists()
        content = json.loads(lock.read_text(encoding="utf-8"))
        assert "reason" in content
        assert content["equity_at_halt"] == 85_000

    def test_lock_not_duplicated(self, dirs, cb):
        data_dir, _ = dirs
        cb.update(100_000)
        cb.update(85_000)
        lock = data_dir / "HALTED.lock"
        mtime1 = lock.stat().st_mtime
        # Second update should not rewrite
        cb.update(84_000)
        mtime2 = lock.stat().st_mtime
        assert mtime1 == mtime2


# ================================================================
# is_halted
# ================================================================


class TestIsHalted:
    def test_false_without_lock(self, cb):
        assert cb.is_halted() is False

    def test_true_with_lock(self, dirs, cb):
        data_dir, _ = dirs
        cb.update(100_000)
        cb.update(85_000)  # triggers lock
        assert cb.is_halted() is True

    def test_persists_after_equity_recovery(self, dirs, cb):
        data_dir, _ = dirs
        cb.update(100_000)
        cb.update(85_000)  # lock written
        # Equity recovers above peak
        cb.update(105_000)
        assert cb.is_halted() is True  # lock file still there

    def test_manual_delete_resumes(self, dirs, cb):
        data_dir, _ = dirs
        cb.update(100_000)
        cb.update(85_000)
        lock = data_dir / "HALTED.lock"
        assert cb.is_halted() is True
        lock.unlink()
        assert cb.is_halted() is False


# ================================================================
# Position size multiplier
# ================================================================


class TestPositionSizeMultiplier:
    def test_normal(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        cb.update(100_000)
        assert cb.get_position_size_multiplier() == 1.0

    def test_reduced_at_daily_reduce(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        cb.update(98_500)  # -1.5%
        assert cb.get_position_size_multiplier() == 0.5

    def test_zero_at_daily_halt(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        cb.update(97_000)  # -3%
        assert cb.get_position_size_multiplier() == 0.0

    def test_zero_at_weekly_halt(self, cb):
        cb.update(100_000)
        cb.reset_weekly()
        cb.update(95_000)  # -5%
        assert cb.get_position_size_multiplier() == 0.0

    def test_zero_at_drawdown_halt(self, cb):
        cb.update(100_000)
        cb.update(90_000)  # -10%
        assert cb.get_position_size_multiplier() == 0.0

    def test_zero_when_lock_exists(self, dirs, cb):
        data_dir, _ = dirs
        cb.update(100_000)
        cb.update(85_000)  # lock
        # Even if we somehow reset state
        cb._status.active_rules = []
        assert cb.get_position_size_multiplier() == 0.0


# ================================================================
# Daily and weekly resets
# ================================================================


class TestResets:
    def test_daily_reset(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        assert cb.day_start_equity == 100_000
        # Small loss from new baseline
        status = cb.update(99_000)
        assert status.daily_pnl_pct == pytest.approx(-1.0, abs=0.01)

    def test_weekly_reset(self, cb):
        cb.update(100_000)
        cb.reset_weekly()
        assert cb.week_start_equity == 100_000
        status = cb.update(96_000)
        assert status.weekly_pnl_pct == pytest.approx(-4.0, abs=0.01)

    def test_daily_reset_clears_previous_daily_loss(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        cb.update(97_000)  # -3% daily
        assert cb._status.status == "RED"
        # New day
        cb.reset_daily()  # day_start = 97_000
        status = cb.update(97_000)
        assert status.daily_pnl_pct == 0.0
        assert "daily_loss_halt" not in status.active_rules


# ================================================================
# State persistence across instantiations
# ================================================================


class TestPersistence:
    def test_state_survives_restart(self, dirs):
        data_dir, snap_dir = dirs
        kwargs = dict(
            data_dir=data_dir, snapshots_dir=snap_dir,
            daily_loss_halt_pct=3.0, daily_loss_reduce_pct=1.5,
            weekly_loss_halt_pct=5.0, drawdown_halt_pct=10.0,
            drawdown_lock_pct=15.0,
        )

        cb1 = CircuitBreaker(**kwargs)
        cb1.update(120_000)
        cb1.reset_daily()
        cb1.reset_weekly()

        # Simulate restart
        cb2 = CircuitBreaker(**kwargs)
        assert cb2.peak_equity == 120_000
        assert cb2.day_start_equity == 120_000
        assert cb2.week_start_equity == 120_000
        assert cb2.current_equity == 120_000

    def test_lock_file_survives_restart(self, dirs):
        data_dir, snap_dir = dirs
        kwargs = dict(
            data_dir=data_dir, snapshots_dir=snap_dir,
            daily_loss_halt_pct=3.0, daily_loss_reduce_pct=1.5,
            weekly_loss_halt_pct=5.0, drawdown_halt_pct=10.0,
            drawdown_lock_pct=15.0,
        )

        cb1 = CircuitBreaker(**kwargs)
        cb1.update(100_000)
        cb1.update(85_000)  # lock

        cb2 = CircuitBreaker(**kwargs)
        assert cb2.is_halted() is True


# ================================================================
# Status dataclass
# ================================================================


class TestCircuitBreakerStatus:
    def test_default_values(self):
        s = CircuitBreakerStatus()
        assert s.status == "GREEN"
        assert s.halted is False
        assert s.active_rules == []
        assert s.daily_pnl == 0.0

    def test_multiple_rules(self, cb):
        cb.update(100_000)
        cb.reset_daily()
        cb.reset_weekly()
        # Down 6% — hits daily halt + weekly halt + drawdown (but not lock)
        status = cb.update(94_000)
        assert "daily_loss_halt" in status.active_rules
        assert "weekly_loss_halt" in status.active_rules
        assert status.status == "RED"
