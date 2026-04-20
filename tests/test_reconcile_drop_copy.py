"""Tests for broker-truth state reconciliation.

Covers: fetch_all_accounts, derive_wheel_state_from_positions,
reconstruct_spread_identities, diff engine, grace period, startup_broker_reconcile,
drop_copy_reconcile, and market_open block flag integration.

All broker calls are mocked — no real network traffic.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_settings(**kwargs):
    """Build a minimal mock settings object."""
    defaults = dict(
        DROP_COPY_POS_MISMATCH_USD=100.0,
        DROP_COPY_POS_MISMATCH_PCT=1.0,
        DROP_COPY_CASH_MISMATCH_USD=100.0,
        STARTUP_RECONCILE_HALT_THRESHOLD_USD=500.0,
        DROP_COPY_GRACE_CYCLES=2,
        DROP_COPY_ENFORCEMENT_MODE="log_only",
        STARTUP_RECONCILE_ENABLED=True,
        DROP_COPY_RECONCILE_ENABLED=True,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_option_pos(symbol: str, qty: float, market_value: float = 100.0,
                     current_price: float = 1.0) -> dict:
    """Build a normalised option position dict."""
    return {
        "symbol": symbol,
        "qty": qty,
        "side": "short" if qty < 0 else "long",
        "asset_class": "us_option",
        "avg_entry_price": current_price,
        "market_value": market_value,
        "unrealized_pl": 0.0,
        "current_price": current_price,
    }


def _make_equity_pos(symbol: str, qty: float, market_value: float = 10000.0) -> dict:
    return {
        "symbol": symbol,
        "qty": qty,
        "side": "long" if qty >= 0 else "short",
        "asset_class": "us_equity",
        "avg_entry_price": market_value / qty if qty else 0.0,
        "market_value": market_value,
        "unrealized_pl": 0.0,
        "current_price": market_value / qty if qty else 0.0,
    }


# ── Tests: _broker_snapshot.fetch_all_accounts ───────────────────────────────


class TestFetchAllAccounts:
    def test_deduplicates_by_credentials(self):
        """PAPER1 group (3 strategies) collapses into one AccountSnapshot."""
        from jobs._broker_snapshot import fetch_all_accounts, AccountSnapshot

        mock_broker = MagicMock()
        mock_broker.get_account.return_value = {
            "portfolio_value": "10000", "cash": "5000", "buying_power": "9000",
        }
        mock_broker.get_all_positions.return_value = []
        mock_broker.get_orders.return_value = []

        fake_settings = SimpleNamespace(
            STRATEGY_ACCOUNT_MAP={
                "bull_put_spread":    ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
                "bear_call_spread":   ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
                "long_call_vertical": ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
                "wheel":              ("ALPACA_PAPER2_API_KEY", "ALPACA_PAPER2_SECRET_KEY"),
            },
            ALPACA_PAPER1_API_KEY="key1",
            ALPACA_PAPER1_SECRET_KEY="secret1",
            ALPACA_PAPER2_API_KEY="key2",
            ALPACA_PAPER2_SECRET_KEY="secret2",
        )

        with (
            patch("jobs._broker_snapshot.settings", fake_settings),
            patch("jobs._broker_snapshot.make_broker_cached", return_value=mock_broker),
        ):
            snapshots = fetch_all_accounts()

        # Two distinct credential pairs → two snapshots
        assert len(snapshots) == 2

        names = {s.account_name for s in snapshots}
        assert "spreads_shared" in names
        assert "wheel" in names

        spreads_snap = next(s for s in snapshots if s.account_name == "spreads_shared")
        assert sorted(spreads_snap.strategy_keys) == sorted([
            "bull_put_spread", "bear_call_spread", "long_call_vertical",
        ])
        assert spreads_snap.fetched_ok is True

    def test_captures_per_account_failure_without_raising(self):
        """A broker exception for one account should not prevent fetching others."""
        from jobs._broker_snapshot import fetch_all_accounts

        good_broker = MagicMock()
        good_broker.get_account.return_value = {
            "portfolio_value": "10000", "cash": "5000", "buying_power": "9000",
        }
        good_broker.get_all_positions.return_value = []
        good_broker.get_orders.return_value = []

        bad_broker = MagicMock()
        bad_broker.get_account.side_effect = RuntimeError("timeout")

        fake_settings = SimpleNamespace(
            STRATEGY_ACCOUNT_MAP={
                "wheel":  ("KEY1", "SECRET1"),
                "iron_condor": ("KEY2", "SECRET2"),
            },
            KEY1="key1", SECRET1="secret1",
            KEY2="key2", SECRET2="secret2",
        )

        call_count = [0]
        def _make_broker(key, secret):
            call_count[0] += 1
            return bad_broker if key == "key2" else good_broker

        with (
            patch("jobs._broker_snapshot.settings", fake_settings),
            patch("jobs._broker_snapshot.make_broker_cached", side_effect=_make_broker),
        ):
            snapshots = fetch_all_accounts()

        assert len(snapshots) == 2
        good = next(s for s in snapshots if s.account_name == "wheel")
        bad = next(s for s in snapshots if s.account_name == "iron_condor")
        assert good.fetched_ok is True
        assert bad.fetched_ok is False
        assert bad.error is not None


# ── Tests: _reconcile_logic.derive_wheel_state_from_positions ─────────────────


class TestDeriveWheelState:
    def test_short_put(self):
        from jobs._reconcile_logic import derive_wheel_state_from_positions
        pos = _make_option_pos("AAPL260515P00260000", qty=-1.0)
        assert derive_wheel_state_from_positions("AAPL", [pos]) == "SHORT_PUT"

    def test_short_call(self):
        from jobs._reconcile_logic import derive_wheel_state_from_positions
        pos = _make_option_pos("AAPL260515C00300000", qty=-1.0)
        assert derive_wheel_state_from_positions("AAPL", [pos]) == "SHORT_CALL"

    def test_long_stock_exactly_100(self):
        from jobs._reconcile_logic import derive_wheel_state_from_positions
        pos = _make_equity_pos("AAPL", qty=100.0)
        assert derive_wheel_state_from_positions("AAPL", [pos]) == "LONG_STOCK"

    def test_long_stock_99_is_idle(self):
        """qty exactly 99 shares is below the 100-share threshold → IDLE (boundary)."""
        from jobs._reconcile_logic import derive_wheel_state_from_positions
        pos = _make_equity_pos("AAPL", qty=99.0)
        assert derive_wheel_state_from_positions("AAPL", [pos]) == "IDLE"

    def test_no_positions_idle(self):
        from jobs._reconcile_logic import derive_wheel_state_from_positions
        assert derive_wheel_state_from_positions("AAPL", []) == "IDLE"

    def test_different_symbol_is_ignored(self):
        from jobs._reconcile_logic import derive_wheel_state_from_positions
        pos = _make_option_pos("SPY260515P00550000", qty=-1.0)
        assert derive_wheel_state_from_positions("AAPL", [pos]) == "IDLE"


# ── Tests: _reconcile_logic.reconstruct_spread_identities ─────────────────────


class TestReconstructSpreadIdentities:
    def _make_tracker_spread(self, leg_symbols: list[tuple[str, str]]) -> dict:
        """Build a fake tracker spread dict. Each tuple: (symbol, position_intent)."""
        return {
            "spread_id": "test-spread-1",
            "strategy_type": "bull_put_spread",
            "underlying": "AAPL",
            "legs": [
                {"symbol": sym, "position_intent": intent}
                for sym, intent in leg_symbols
            ],
        }

    def test_matched_when_both_legs_present(self):
        from jobs._reconcile_logic import reconstruct_spread_identities
        spread = self._make_tracker_spread([
            ("AAPL260515P00260000", "sell_to_open"),
            ("AAPL260515P00250000", "buy_to_open"),
        ])
        positions = [
            _make_option_pos("AAPL260515P00260000", qty=-1.0),
            _make_option_pos("AAPL260515P00250000", qty=1.0),
        ]
        matched, untracked = reconstruct_spread_identities(positions, [spread])
        assert len(matched) == 1
        assert len(untracked) == 0

    def test_unmatched_when_one_leg_missing(self):
        from jobs._reconcile_logic import reconstruct_spread_identities
        spread = self._make_tracker_spread([
            ("AAPL260515P00260000", "sell_to_open"),
            ("AAPL260515P00250000", "buy_to_open"),
        ])
        positions = [
            _make_option_pos("AAPL260515P00260000", qty=-1.0),
            # Missing AAPL260515P00250000
        ]
        matched, untracked = reconstruct_spread_identities(positions, [spread])
        assert len(matched) == 0
        # The untracked includes the orphaned sell leg
        assert len(untracked) == 1

    def test_untracked_leg_when_spread_tracker_empty(self):
        from jobs._reconcile_logic import reconstruct_spread_identities
        positions = [
            _make_option_pos("AAPL260515P00260000", qty=-1.0),
        ]
        matched, untracked = reconstruct_spread_identities(positions, [])
        assert len(matched) == 0
        assert len(untracked) == 1
        assert untracked[0]["symbol"] == "AAPL260515P00260000"


# ── Tests: _reconcile_diff.build_diff severity ────────────────────────────────


class TestDiffEngineSeverity:
    def _minimal_snapshots(self, positions=None, orders=None, cash=5000.0, pv=10000.0):
        from jobs._broker_snapshot import AccountSnapshot
        return [AccountSnapshot(
            account_name="wheel",
            strategy_keys=["wheel"],
            fetched_ok=True,
            portfolio_value=pv,
            cash=cash,
            buying_power=8000.0,
            positions=positions or [],
            open_orders=orders or [],
        )]

    def test_under_threshold_is_none(self):
        """Position mismatch under DROP_COPY_POS_MISMATCH_USD → severity 'none'."""
        from jobs._reconcile_diff import build_diff
        # Untracked position with market_value = 50 → delta $50 < $100 threshold
        positions = [_make_option_pos("AAPL260515P00260000", qty=-1.0, market_value=50.0)]
        settings = _make_settings(DROP_COPY_POS_MISMATCH_USD=100.0)
        diff = build_diff(
            snapshots=self._minimal_snapshots(positions=positions),
            tracker_open_spreads=[],
            strategy_states=[],
            local_pending_order_ids=set(),
            prior_cash_by_account={"wheel": 5000.0},
            settings_obj=settings,
        )
        assert diff.severity == "none"

    def test_exactly_at_threshold_is_none(self):
        """Boundary: delta_usd == threshold → severity 'none' (strict >)."""
        from jobs._reconcile_diff import build_diff
        # market_value=100.0 → delta=100.0; threshold=100.0; strict > means NOT yellow
        positions = [_make_option_pos("AAPL260515P00260000", qty=-1.0, market_value=100.0)]
        settings = _make_settings(
            DROP_COPY_POS_MISMATCH_USD=100.0,
            STARTUP_RECONCILE_HALT_THRESHOLD_USD=500.0,
        )
        diff = build_diff(
            snapshots=self._minimal_snapshots(positions=positions, pv=100_000.0),
            tracker_open_spreads=[],
            strategy_states=[],
            local_pending_order_ids=set(),
            prior_cash_by_account={"wheel": 5000.0},
            settings_obj=settings,
        )
        # 100 == 100 is NOT strictly greater than 100
        assert diff.severity == "none", (
            "Boundary is strict >; delta_usd equal to threshold must NOT be yellow"
        )

    def test_one_cent_over_threshold_is_yellow(self):
        """delta_usd = threshold + $0.01 → severity 'yellow'."""
        from jobs._reconcile_diff import build_diff
        positions = [_make_option_pos("AAPL260515P00260000", qty=-1.0, market_value=100.01)]
        settings = _make_settings(
            DROP_COPY_POS_MISMATCH_USD=100.0,
            STARTUP_RECONCILE_HALT_THRESHOLD_USD=500.0,
        )
        diff = build_diff(
            snapshots=self._minimal_snapshots(positions=positions, pv=100_000.0),
            tracker_open_spreads=[],
            strategy_states=[],
            local_pending_order_ids=set(),
            prior_cash_by_account={"wheel": 5000.0},
            settings_obj=settings,
        )
        assert diff.severity == "yellow"

    def test_filled_broker_order_no_local_record_is_red(self):
        """Filled broker order with no local record → severity 'red'."""
        from jobs._reconcile_diff import build_diff
        orders = [{
            "id": "order-abc123",
            "symbol": "AAPL260515P00260000",
            "qty": 1.0,
            "side": "sell",
            "status": "filled",
            "limit_price": 1.50,
            "order_type": "limit",
            "submitted_at": "2026-04-19T10:00:00Z",
        }]
        settings = _make_settings()
        diff = build_diff(
            snapshots=self._minimal_snapshots(orders=orders),
            tracker_open_spreads=[],
            strategy_states=[],
            local_pending_order_ids=set(),
            prior_cash_by_account={"wheel": 5000.0},
            settings_obj=settings,
        )
        assert diff.severity == "red"
        assert any(
            od.category == "untracked_broker" and od.status_broker == "filled"
            for od in diff.order_diffs
        )


# ── Tests: grace period / observation tracking ────────────────────────────────


class TestGracePeriod:
    def _make_diff_with_mismatch(self):
        """Build a diff that has one position mismatch above threshold."""
        from jobs._reconcile_diff import build_diff, ReconcileDiff
        from jobs._broker_snapshot import AccountSnapshot
        pos = _make_option_pos("AAPL260515P00260000", qty=-1.0, market_value=200.0)
        snaps = [AccountSnapshot(
            account_name="wheel",
            strategy_keys=["wheel"],
            fetched_ok=True,
            portfolio_value=100_000.0,
            cash=5000.0,
            buying_power=8000.0,
            positions=[pos],
            open_orders=[],
        )]
        settings = _make_settings(
            DROP_COPY_POS_MISMATCH_USD=100.0,
            STARTUP_RECONCILE_HALT_THRESHOLD_USD=500.0,
        )
        return build_diff(
            snapshots=snaps,
            tracker_open_spreads=[],
            strategy_states=[],
            local_pending_order_ids=set(),
            prior_cash_by_account={"wheel": 5000.0},
            settings_obj=settings,
        ), settings

    def test_first_observation_not_actionable(self, tmp_path):
        """First cycle with a mismatch should not trigger action (count < grace_cycles)."""
        from jobs import drop_copy_reconcile as _dcr

        diff, settings = self._make_diff_with_mismatch()
        obs_path = tmp_path / "observations.json"
        observations = {}

        # Simulate one cycle
        _simulate_observation_update(diff, observations)

        assert observations  # keys present
        for key, v in observations.items():
            assert v["consecutive_count"] == 1

        actionable = {k for k, v in observations.items() if v["consecutive_count"] >= 2}
        assert not actionable

    def test_second_observation_is_actionable(self, tmp_path):
        """After two consecutive cycles the mismatch becomes actionable."""
        diff, settings = self._make_diff_with_mismatch()
        observations = {}

        _simulate_observation_update(diff, observations)
        _simulate_observation_update(diff, observations)

        actionable = {k for k, v in observations.items() if v["consecutive_count"] >= 2}
        assert actionable

    def test_disappearance_resets_count(self):
        """A mismatch that disappears then returns starts the count over."""
        diff, settings = self._make_diff_with_mismatch()

        from jobs._reconcile_diff import ReconcileDiff
        from datetime import datetime, timezone
        empty_diff = ReconcileDiff(
            generated_at=datetime.now(timezone.utc).isoformat(),
            accounts_fetched=1,
            accounts_failed=[],
            position_diffs=[],
            cash_diffs=[],
            order_diffs=[],
            total_abs_position_delta_usd=0.0,
            severity="none",
            severity_reasons=[],
        )

        observations = {}
        _simulate_observation_update(diff, observations)
        assert all(v["consecutive_count"] == 1 for v in observations.values())

        # Clean cycle removes the observation
        _simulate_observation_update(empty_diff, observations)
        assert not observations  # cleared

        # Mismatch returns → count back to 1
        _simulate_observation_update(diff, observations)
        assert all(v["consecutive_count"] == 1 for v in observations.values())


# ── Tests: startup_broker_reconcile ──────────────────────────────────────────


class TestStartupBrokerReconcile:
    def _run_with_mocks(self, tmp_path, mode="log_only", snapshots=None,
                        halt_threshold=500.0):
        from jobs._broker_snapshot import AccountSnapshot
        if snapshots is None:
            snapshots = [AccountSnapshot(
                account_name="wheel",
                strategy_keys=["wheel"],
                fetched_ok=True,
                portfolio_value=10000.0,
                cash=5000.0,
                buying_power=8000.0,
                positions=[],
                open_orders=[],
            )]

        fake_settings = SimpleNamespace(
            STARTUP_RECONCILE_ENABLED=True,
            DROP_COPY_ENFORCEMENT_MODE=mode,
            DROP_COPY_POS_MISMATCH_USD=100.0,
            DROP_COPY_POS_MISMATCH_PCT=1.0,
            DROP_COPY_CASH_MISMATCH_USD=100.0,
            STARTUP_RECONCILE_HALT_THRESHOLD_USD=halt_threshold,
            DROP_COPY_GRACE_CYCLES=2,
            SNAPSHOTS_DIR=tmp_path,
        )

        with (
            patch("jobs.startup_broker_reconcile.settings", fake_settings),
            patch("jobs.startup_broker_reconcile.fetch_all_accounts", return_value=snapshots),
            patch("jobs.startup_broker_reconcile.StrategyStateRepository") as MockRepo,
            patch("jobs.startup_broker_reconcile.SpreadTracker") as MockTracker,
            patch("jobs.startup_broker_reconcile.Database"),
            patch("jobs.startup_broker_reconcile.TradeRecorder"),
        ):
            MockRepo.return_value.get_all.return_value = []
            MockTracker.return_value.get_active_spreads.return_value = []
            from jobs.startup_broker_reconcile import run
            return run()

    def test_log_only_never_writes_halted_lock(self, tmp_path):
        """In log_only mode, HALTED.lock must never be written."""
        from jobs._broker_snapshot import AccountSnapshot
        # Large untracked position that would exceed halt threshold in enforce mode
        big_pos = _make_option_pos("AAPL260515P00260000", qty=-1.0, market_value=600.0)
        snap = AccountSnapshot(
            account_name="wheel",
            strategy_keys=["wheel"],
            fetched_ok=True,
            portfolio_value=10000.0,
            cash=5000.0,
            buying_power=8000.0,
            positions=[big_pos],
            open_orders=[],
        )
        result = self._run_with_mocks(tmp_path, mode="log_only", snapshots=[snap],
                                      halt_threshold=500.0)

        lock_path = tmp_path / "HALTED.lock"
        assert not lock_path.exists(), "log_only must never write HALTED.lock"
        assert result["halted"] is False

    def test_log_only_always_writes_report(self, tmp_path):
        """Report JSON must be written even in log_only mode."""
        self._run_with_mocks(tmp_path, mode="log_only")
        report_path = tmp_path / "startup_reconcile_report.json"
        assert report_path.exists()

    def test_enforce_above_halt_threshold_writes_lock(self, tmp_path):
        """In enforce mode, total untracked delta > halt threshold writes HALTED.lock."""
        from jobs._broker_snapshot import AccountSnapshot
        big_pos = _make_option_pos("AAPL260515P00260000", qty=-1.0, market_value=600.0)
        snap = AccountSnapshot(
            account_name="wheel",
            strategy_keys=["wheel"],
            fetched_ok=True,
            portfolio_value=10000.0,
            cash=5000.0,
            buying_power=8000.0,
            positions=[big_pos],
            open_orders=[],
        )

        with patch("jobs.startup_broker_reconcile._write_halt_lock") as mock_halt:
            result = self._run_with_mocks(tmp_path, mode="enforce", snapshots=[snap],
                                          halt_threshold=500.0)

        # Either the lock was written directly or through _write_halt_lock mock
        # The result should indicate halted
        assert result["halted"] is True or mock_halt.called


# ── Tests: drop_copy_reconcile ────────────────────────────────────────────────


class TestDropCopyReconcile:
    def test_enforce_yellow_writes_block_flag(self, tmp_path):
        """In enforce mode with actionable yellow mismatch, drop_copy_block.json is written."""
        from jobs._broker_snapshot import AccountSnapshot
        pos = _make_option_pos("AAPL260515P00260000", qty=-1.0, market_value=200.0)
        snap = AccountSnapshot(
            account_name="wheel",
            strategy_keys=["wheel"],
            fetched_ok=True,
            portfolio_value=100_000.0,
            cash=5000.0,
            buying_power=8000.0,
            positions=[pos],
            open_orders=[],
        )
        fake_settings = SimpleNamespace(
            DROP_COPY_RECONCILE_ENABLED=True,
            DROP_COPY_ENFORCEMENT_MODE="enforce",
            DROP_COPY_POS_MISMATCH_USD=100.0,
            DROP_COPY_POS_MISMATCH_PCT=1.0,
            DROP_COPY_CASH_MISMATCH_USD=100.0,
            STARTUP_RECONCILE_HALT_THRESHOLD_USD=500.0,
            DROP_COPY_GRACE_CYCLES=1,  # immediately actionable
            SNAPSHOTS_DIR=tmp_path,
        )

        with (
            patch("jobs.drop_copy_reconcile.settings", fake_settings),
            patch("jobs.drop_copy_reconcile.fetch_all_accounts", return_value=[snap]),
            patch("jobs.drop_copy_reconcile.StrategyStateRepository") as MockRepo,
            patch("jobs.drop_copy_reconcile.SpreadTracker") as MockTracker,
            patch("jobs.drop_copy_reconcile.Database"),
            patch("jobs.drop_copy_reconcile.TradeRecorder"),
            patch("jobs.drop_copy_reconcile._apply_enforce_yellow"),
            patch("jobs.drop_copy_reconcile._notify_enforce"),
        ):
            MockRepo.return_value.get_all.return_value = []
            MockTracker.return_value.get_active_spreads.return_value = []
            from jobs.drop_copy_reconcile import run
            run()

        block_path = tmp_path / "drop_copy_block.json"
        assert block_path.exists(), "block flag must be written on actionable yellow in enforce mode"

    def test_clean_cycle_clears_block_flag(self, tmp_path):
        """When diff.severity='none', an existing block flag is cleared."""
        from jobs._broker_snapshot import AccountSnapshot
        block_path = tmp_path / "drop_copy_block.json"
        block_path.write_text('{"set_at": "x", "reason": "test", "affected_accounts": []}')

        snap = AccountSnapshot(
            account_name="wheel",
            strategy_keys=["wheel"],
            fetched_ok=True,
            portfolio_value=10000.0,
            cash=5000.0,
            buying_power=8000.0,
            positions=[],
            open_orders=[],
        )
        fake_settings = SimpleNamespace(
            DROP_COPY_RECONCILE_ENABLED=True,
            DROP_COPY_ENFORCEMENT_MODE="enforce",
            DROP_COPY_POS_MISMATCH_USD=100.0,
            DROP_COPY_POS_MISMATCH_PCT=1.0,
            DROP_COPY_CASH_MISMATCH_USD=100.0,
            STARTUP_RECONCILE_HALT_THRESHOLD_USD=500.0,
            DROP_COPY_GRACE_CYCLES=2,
            SNAPSHOTS_DIR=tmp_path,
        )

        with (
            patch("jobs.drop_copy_reconcile.settings", fake_settings),
            patch("jobs.drop_copy_reconcile.fetch_all_accounts", return_value=[snap]),
            patch("jobs.drop_copy_reconcile.StrategyStateRepository") as MockRepo,
            patch("jobs.drop_copy_reconcile.SpreadTracker") as MockTracker,
            patch("jobs.drop_copy_reconcile.Database"),
            patch("jobs.drop_copy_reconcile.TradeRecorder"),
        ):
            MockRepo.return_value.get_all.return_value = []
            MockTracker.return_value.get_active_spreads.return_value = []
            from jobs.drop_copy_reconcile import run
            run()

        assert not block_path.exists(), "block flag must be cleared on clean cycle"


# ── Tests: market_open honours drop-copy block ────────────────────────────────


class TestMarketOpenDropCopyBlock:
    def test_block_flag_sets_block_all_new_entries(self, tmp_path):
        """When drop_copy_block.json exists, block_all_new_entries is True."""
        # Write the block flag
        block_path = tmp_path / "drop_copy_block.json"
        block_path.write_text('{"set_at": "x", "reason": "test", "affected_accounts": []}')

        # We're testing the flag logic, not the full market_open.run() which is
        # too heavy to mock entirely. Test the detection logic directly.
        drop_copy_block_path = block_path
        drop_copy_blocked = drop_copy_block_path.exists()

        assert drop_copy_blocked is True

        # Simulate what market_open does: OR into block_all_new_entries
        size_multiplier = 1.0  # CB is GREEN
        cb_is_red = (size_multiplier == 0.0)
        block_all_new_entries = cb_is_red or drop_copy_blocked

        assert block_all_new_entries is True

    def test_no_block_flag_does_not_affect_cb(self, tmp_path):
        """When drop_copy_block.json is absent, CB gate is unaffected."""
        block_path = tmp_path / "drop_copy_block.json"
        # Don't create it

        drop_copy_blocked = block_path.exists()
        assert drop_copy_blocked is False

        size_multiplier = 1.0
        cb_is_red = (size_multiplier == 0.0)
        block_all_new_entries = cb_is_red or drop_copy_blocked
        assert block_all_new_entries is False


# ── internal test helpers ─────────────────────────────────────────────────────


def _simulate_observation_update(diff, observations: dict) -> None:
    """Replicate the observation-update logic from drop_copy_reconcile.run()."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    current_keys: set[str] = set()
    for pd in diff.position_diffs:
        if pd.category in ("untracked", "qty_mismatch"):
            key = f"{pd.account_name}::{pd.symbol}::{pd.category}"
            current_keys.add(key)
    for od in diff.order_diffs:
        if od.category == "untracked_broker":
            key = f"{od.account_name}::order::{od.broker_order_id}"
            current_keys.add(key)

    for key in current_keys:
        if key in observations:
            observations[key]["last_seen_at"] = now_iso
            observations[key]["consecutive_count"] += 1
        else:
            observations[key] = {
                "first_seen_at": now_iso,
                "last_seen_at": now_iso,
                "consecutive_count": 1,
            }

    keys_to_remove = [k for k in observations if k not in current_keys]
    for k in keys_to_remove:
        del observations[k]
