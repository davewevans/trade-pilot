"""Tests for jobs.startup_reconciler."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── helpers ──────────────────────────────────────────────────


def _make_alpaca_order(order_id: str, status: str, symbol: str = "AAPL240101C00150000"):
    return {
        "id": order_id,
        "status": status,
        "symbol": symbol,
        "submitted_at": "2026-04-17T10:00:00+00:00",
        "filled_at": "2026-04-17T10:00:01+00:00" if status == "filled" else None,
        "filled_avg_price": "1.50" if status == "filled" else None,
        "asset_class": "us_option",
    }


def _mock_broker(open_orders=None, closed_orders=None, raises=False):
    broker = MagicMock()
    if raises:
        broker.get_orders.side_effect = RuntimeError("API down")
        broker.get_account.side_effect = RuntimeError("API down")
    else:
        def get_orders(status="open", limit=50):
            if status == "open":
                return open_orders or []
            return closed_orders or []
        broker.get_orders.side_effect = get_orders
        broker.get_account.return_value = {"portfolio_value": 10000}
    return broker


# ── fixtures ─────────────────────────────────────────────────


@pytest.fixture
def tmp_state(tmp_path):
    """Yield a tmp_path and patch settings.DATA_DIR / SNAPSHOTS_DIR to tmp dirs."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    snaps_dir = data_dir / "snapshots"
    snaps_dir.mkdir()
    return tmp_path, data_dir, snaps_dir


# ── tests ─────────────────────────────────────────────────────


class TestFilledEntryUpdatesToOpen:
    def test_filled_entry_updates_to_open(self, tmp_state):
        """A PENDING_OPEN spread with a filled Alpaca order → OPEN."""
        _, data_dir, snaps_dir = tmp_state

        order_id = "order-fill-001"
        spread_id = "spread-abc"

        # Seed spread tracker with PENDING_OPEN
        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text(json.dumps([{
            "spread_id": spread_id,
            "strategy_type": "bull_put_spread",
            "underlying": "AAPL",
            "legs": [],
            "entry_credit": 1.0,
            "entry_date": "2026-04-17",
            "expiration": "2026-05-17",
            "max_loss": 400,
            "max_gain": 100,
            "status": "pending_open",
            "entry_order_id": order_id,
            "close_order_id": None,
            "registered_at": "2026-04-17T10:00:00",
        }]), encoding="utf-8")

        alpaca_order = _make_alpaca_order(order_id, "filled")
        broker = _mock_broker(closed_orders=[alpaca_order])

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", return_value=broker), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls:

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir

            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_conn = MagicMock()
            mock_db.get_connection.return_value = mock_conn

            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            summary = run()

        # Re-read the spreads file and check status
        updated = json.loads(spreads_path.read_text(encoding="utf-8"))
        assert any(s["spread_id"] == spread_id and s["status"] == "open" for s in updated), \
            f"Expected OPEN status, got: {[s['status'] for s in updated]}"
        assert summary["orders_reconciled"] >= 1


class TestFilledExitUpdatesToClosed:
    def test_filled_exit_updates_to_closed(self, tmp_state):
        """A PENDING_CLOSE spread with a filled Alpaca order → CLOSED."""
        _, data_dir, snaps_dir = tmp_state

        order_id = "order-close-001"
        spread_id = "spread-close-abc"

        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text(json.dumps([{
            "spread_id": spread_id,
            "strategy_type": "bull_put_spread",
            "underlying": "SPY",
            "legs": [],
            "entry_credit": 1.5,
            "entry_date": "2026-04-10",
            "expiration": "2026-05-17",
            "max_loss": 350,
            "max_gain": 150,
            "status": "pending_close",
            "entry_order_id": "order-entry-001",
            "close_order_id": order_id,
            "registered_at": "2026-04-10T10:00:00",
        }]), encoding="utf-8")

        alpaca_order = _make_alpaca_order(order_id, "filled")
        broker = _mock_broker(closed_orders=[alpaca_order])

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", return_value=broker), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls:

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir

            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_conn = MagicMock()
            mock_db.get_connection.return_value = mock_conn

            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            summary = run()

        updated = json.loads(spreads_path.read_text(encoding="utf-8"))
        assert any(s["spread_id"] == spread_id and s["status"] == "closed" for s in updated), \
            f"Expected CLOSED, got: {[s['status'] for s in updated]}"
        assert summary["orders_reconciled"] >= 1


class TestCanceledRevertsToIdle:
    def test_canceled_reverts_to_idle(self, tmp_state):
        """A PENDING_OPEN spread with a canceled Alpaca order → CANCELED."""
        _, data_dir, snaps_dir = tmp_state

        order_id = "order-cancel-001"
        spread_id = "spread-cancel-abc"

        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text(json.dumps([{
            "spread_id": spread_id,
            "strategy_type": "iron_condor",
            "underlying": "SPY",
            "legs": [],
            "entry_credit": 2.0,
            "entry_date": "2026-04-17",
            "expiration": "2026-05-17",
            "max_loss": 300,
            "max_gain": 200,
            "status": "pending_open",
            "entry_order_id": order_id,
            "close_order_id": None,
            "registered_at": "2026-04-17T10:00:00",
        }]), encoding="utf-8")

        alpaca_order = _make_alpaca_order(order_id, "canceled")
        broker = _mock_broker(closed_orders=[alpaca_order])

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", return_value=broker), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls:

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_db.get_connection.return_value = MagicMock()
            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            summary = run()

        updated = json.loads(spreads_path.read_text(encoding="utf-8"))
        assert any(s["spread_id"] == spread_id and s["status"] == "canceled" for s in updated), \
            f"Expected CANCELED, got: {[s['status'] for s in updated]}"

    def test_rejected_reverts_to_idle(self, tmp_state):
        """A PENDING_OPEN spread with a rejected Alpaca order → CANCELED."""
        _, data_dir, snaps_dir = tmp_state

        order_id = "order-reject-001"
        spread_id = "spread-reject-abc"

        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text(json.dumps([{
            "spread_id": spread_id,
            "strategy_type": "iron_condor",
            "underlying": "QQQ",
            "legs": [],
            "entry_credit": 1.8,
            "entry_date": "2026-04-17",
            "expiration": "2026-05-17",
            "max_loss": 320,
            "max_gain": 180,
            "status": "pending_open",
            "entry_order_id": order_id,
            "close_order_id": None,
            "registered_at": "2026-04-17T10:00:00",
        }]), encoding="utf-8")

        alpaca_order = _make_alpaca_order(order_id, "rejected")
        broker = _mock_broker(closed_orders=[alpaca_order])

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", return_value=broker), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls:

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_db.get_connection.return_value = MagicMock()
            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            summary = run()

        updated = json.loads(spreads_path.read_text(encoding="utf-8"))
        assert any(s["spread_id"] == spread_id and s["status"] == "canceled" for s in updated), \
            f"Expected CANCELED, got: {[s['status'] for s in updated]}"


class TestWorkingOrderLeftAlone:
    def test_working_order_left_alone(self, tmp_state):
        """A PENDING_OPEN spread whose Alpaca order is still working → unchanged."""
        _, data_dir, snaps_dir = tmp_state

        order_id = "order-working-001"
        spread_id = "spread-working-abc"

        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text(json.dumps([{
            "spread_id": spread_id,
            "strategy_type": "bull_put_spread",
            "underlying": "AAPL",
            "legs": [],
            "entry_credit": 1.0,
            "entry_date": "2026-04-17",
            "expiration": "2026-05-17",
            "max_loss": 400,
            "max_gain": 100,
            "status": "pending_open",
            "entry_order_id": order_id,
            "close_order_id": None,
            "registered_at": "2026-04-17T10:00:00",
        }]), encoding="utf-8")

        alpaca_order = _make_alpaca_order(order_id, "new")  # working
        broker = _mock_broker(open_orders=[alpaca_order])

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", return_value=broker), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls:

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_db.get_connection.return_value = MagicMock()
            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            run()

        updated = json.loads(spreads_path.read_text(encoding="utf-8"))
        assert any(s["spread_id"] == spread_id and s["status"] == "pending_open" for s in updated), \
            f"Expected PENDING_OPEN unchanged, got: {[s['status'] for s in updated]}"


class TestOrphanAlpacaOrderLogsWarning:
    def test_orphan_alpaca_order_logs_warning(self, tmp_state, caplog):
        """Alpaca order with no local match → warning logged, orphan counted."""
        _, data_dir, snaps_dir = tmp_state

        # Empty spread tracker
        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text("[]", encoding="utf-8")

        orphan_order_id = "order-orphan-001"
        alpaca_order = _make_alpaca_order(orphan_order_id, "filled")
        broker = _mock_broker(closed_orders=[alpaca_order])

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", return_value=broker), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls, \
             caplog.at_level(logging.WARNING):

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_db.get_connection.return_value = MagicMock()
            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            summary = run()

        assert summary["orphan_orders_on_alpaca"] >= 1
        assert any("orphan" in r.message.lower() and orphan_order_id in r.message
                   for r in caplog.records), \
            f"Expected orphan warning with order id. Got: {[r.message for r in caplog.records]}"


class TestOrphanLocalPendingLogsWarning:
    def test_orphan_local_pending_logs_warning(self, tmp_state, caplog):
        """Local PENDING spread with no Alpaca match → warning logged."""
        _, data_dir, snaps_dir = tmp_state

        orphan_order_id = "order-local-orphan-001"
        spread_id = "spread-local-orphan"

        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text(json.dumps([{
            "spread_id": spread_id,
            "strategy_type": "bull_put_spread",
            "underlying": "AAPL",
            "legs": [],
            "entry_credit": 1.0,
            "entry_date": "2026-04-17",
            "expiration": "2026-05-17",
            "max_loss": 400,
            "max_gain": 100,
            "status": "pending_open",
            "entry_order_id": orphan_order_id,
            "close_order_id": None,
            "registered_at": "2026-04-17T10:00:00",
        }]), encoding="utf-8")

        # Alpaca returns NO orders for this account
        broker = _mock_broker(open_orders=[], closed_orders=[])

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", return_value=broker), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls, \
             caplog.at_level(logging.WARNING):

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_db.get_connection.return_value = MagicMock()
            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            summary = run()

        assert summary["orphan_pending_local"] >= 1
        assert any("orphan" in r.message.lower() for r in caplog.records), \
            f"Expected orphan warning. Got: {[r.message for r in caplog.records]}"


class TestAccountFailureContinuesToNextAccount:
    def test_account_failure_continues_to_next_account(self, tmp_state):
        """First account raises on make_broker → second account still queried."""
        _, data_dir, snaps_dir = tmp_state

        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text("[]", encoding="utf-8")

        good_broker = _mock_broker(open_orders=[], closed_orders=[])
        call_log = []

        def make_broker_side_effect(strategy_name):
            call_log.append(strategy_name)
            if strategy_name == "wheel":
                raise RuntimeError("credentials missing")
            return good_broker

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", side_effect=make_broker_side_effect), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls:

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_db.get_connection.return_value = MagicMock()
            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            summary = run()

        assert "wheel" in summary["account_failures"]
        # Other accounts should still have been tried
        assert any(k in call_log for k in ("iron_condor", "bull_put_spread"))
        assert summary["accounts_checked"] >= 2  # at least 2 of 3 succeeded


class TestReconcilerDoesNotCallExecuteDecision:
    def test_reconciler_does_not_call_execute_decision(self):
        """The source of startup_reconciler's run() function must not contain order-creation calls."""
        import inspect
        from jobs import startup_reconciler
        # Check only the run() function source, not the module docstring which
        # mentions these function names in the "HARD CONSTRAINT" comment.
        source = inspect.getsource(startup_reconciler.run)
        forbidden = ["execute_decision", "place_order", "submit_order"]
        for fn_name in forbidden:
            assert fn_name not in source, \
                f"startup_reconciler.run() must not call {fn_name!r} — found in function source"


class TestReconcilerRunsWithHaltedLock:
    def test_reconciler_runs_with_halted_lock_present(self, tmp_state, tmp_path):
        """HALTED.lock does not prevent the reconciler from running."""
        _, data_dir, snaps_dir = tmp_state

        # Write a HALTED.lock file
        lock_file = data_dir / "HALTED.lock"
        lock_file.write_text(json.dumps({"reason": "test"}), encoding="utf-8")
        assert lock_file.exists()

        spreads_path = snaps_dir / "open_spreads.json"
        spreads_path.write_text("[]", encoding="utf-8")

        broker = _mock_broker(open_orders=[], closed_orders=[])

        with patch("config.settings") as mock_settings, \
             patch("brokers.broker_factory.make_broker", return_value=broker), \
             patch("database.db.Database") as mock_db_cls, \
             patch("database.recorder.TradeRecorder") as mock_recorder_cls:

            mock_settings.DATA_DIR = data_dir
            mock_settings.SNAPSHOTS_DIR = snaps_dir
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db
            mock_db.get_connection.return_value = MagicMock()
            mock_recorder = MagicMock()
            mock_recorder.trades.get_pending.return_value = []
            mock_recorder_cls.return_value = mock_recorder

            from jobs.startup_reconciler import run
            # Must complete without exception
            summary = run()

        assert isinstance(summary, dict)
        assert lock_file.exists()  # lock file was not deleted
