"""Tests for the pending-order reconciler and TradeRecorder.resolve_trade."""

import os
import sqlite3
import tempfile
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from database.db import Database
from database.recorder import TradeRecorder
from jobs.reconcile_orders import reconcile_pending_orders


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def recorder(tmp_path):
    """Fresh DB + recorder, isolated per test."""
    db_path = tmp_path / "test.db"
    db = Database(path=str(db_path))
    db.init_schema()
    rec = TradeRecorder(db.get_connection())
    yield rec
    db.close()


def _seed_pending_trade(
    recorder,
    *,
    order_id: str,
    submitted_at: str,
    underlying: str = "AAPL",
    symbol: str = "AAPL260515P00185000",
):
    """Insert a pending trade, bypassing the recorder's auto-cycle path
    so submitted_at is fully under our control.
    """
    cycle_id = recorder.cycles.insert({
        "strategy_type": "wheel",
        "underlying": underlying,
    })
    recorder.trades.insert({
        "cycle_id": cycle_id,
        "alpaca_order_id": order_id,
        "underlying": underlying,
        "strategy_type": "wheel",
        "trade_type": "SELL_PUT",
        "symbol": symbol,
        "limit_price": 1.25,
        "submitted_at": submitted_at,
        "fill_status": "pending",
    })
    return cycle_id


# ── resolve_trade ──────────────────────────────────────────


def test_resolve_trade_filled_sets_price_and_timestamp(recorder):
    today_iso = date.today().isoformat() + "T10:00:00"
    _seed_pending_trade(recorder, order_id="ord_filled", submitted_at=today_iso)

    ok = recorder.resolve_trade("ord_filled", "filled", fill_price=1.20)
    assert ok is True

    row = recorder._conn.execute(
        "SELECT fill_status, fill_price, filled_at FROM trades WHERE alpaca_order_id=?",
        ("ord_filled",),
    ).fetchone()
    assert row["fill_status"] == "filled"
    assert row["fill_price"] == 1.20
    assert row["filled_at"] is not None
    assert "T" in row["filled_at"]  # ISO timestamp


def test_resolve_trade_canceled_leaves_fill_price_null(recorder):
    today_iso = date.today().isoformat() + "T10:00:00"
    _seed_pending_trade(recorder, order_id="ord_cxl", submitted_at=today_iso)

    ok = recorder.resolve_trade("ord_cxl", "canceled")
    assert ok is True

    row = recorder._conn.execute(
        "SELECT fill_status, fill_price, filled_at FROM trades WHERE alpaca_order_id=?",
        ("ord_cxl",),
    ).fetchone()
    assert row["fill_status"] == "canceled"
    assert row["fill_price"] is None
    assert row["filled_at"] is None


def test_resolve_trade_unknown_order_id_returns_false(recorder, caplog):
    caplog.set_level("WARNING")
    ok = recorder.resolve_trade("does_not_exist", "filled", fill_price=1.0)
    assert ok is False
    assert any("no row found" in r.message for r in caplog.records)


def test_resolve_trade_db_failure_returns_false(recorder, caplog):
    caplog.set_level("ERROR")
    # Force a failure: close the connection so the next execute raises.
    recorder._conn.close()

    ok = recorder.resolve_trade("anything", "filled", fill_price=1.0)
    assert ok is False
    assert any("resolve_trade failed" in r.message for r in caplog.records)


# ── reconcile_pending_orders ───────────────────────────────


def test_reconciler_today_only_processes_today_rows(recorder, caplog):
    caplog.set_level("WARNING")
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    _seed_pending_trade(recorder, order_id="ord_today", submitted_at=f"{today}T10:00:00")
    _seed_pending_trade(recorder, order_id="ord_old_1", submitted_at=f"{yesterday}T10:00:00")
    _seed_pending_trade(recorder, order_id="ord_old_2", submitted_at=f"{yesterday}T11:00:00")

    broker = MagicMock()
    broker.get_order.return_value = {"status": "filled", "filled_avg_price": "1.10"}

    reconcile_pending_orders(broker, recorder)

    # Broker only called for today's row
    broker.get_order.assert_called_once_with("ord_today")

    # Today's row resolved
    row_today = recorder._conn.execute(
        "SELECT fill_status, fill_price FROM trades WHERE alpaca_order_id='ord_today'"
    ).fetchone()
    assert row_today["fill_status"] == "filled"
    assert row_today["fill_price"] == 1.10

    # Stale rows untouched
    for oid in ("ord_old_1", "ord_old_2"):
        r = recorder._conn.execute(
            "SELECT fill_status FROM trades WHERE alpaca_order_id=?", (oid,),
        ).fetchone()
        assert r["fill_status"] == "pending"

    # Stale-count warning emitted
    assert any(
        "2 pending trades from prior days" in r.message for r in caplog.records
    )


def test_reconciler_with_no_pending_does_not_call_broker(recorder):
    broker = MagicMock()
    reconcile_pending_orders(broker, recorder)
    broker.get_order.assert_not_called()


def test_reconciler_unknown_broker_status_leaves_row_pending(recorder):
    today = date.today().isoformat()
    _seed_pending_trade(recorder, order_id="ord_open", submitted_at=f"{today}T10:00:00")

    broker = MagicMock()
    broker.get_order.return_value = {"status": "accepted"}

    reconcile_pending_orders(broker, recorder)

    row = recorder._conn.execute(
        "SELECT fill_status, fill_price, filled_at FROM trades WHERE alpaca_order_id='ord_open'"
    ).fetchone()
    assert row["fill_status"] == "pending"
    assert row["fill_price"] is None
    assert row["filled_at"] is None


def test_reconciler_handles_canceled_status(recorder):
    today = date.today().isoformat()
    _seed_pending_trade(recorder, order_id="ord_cxl_today", submitted_at=f"{today}T10:00:00")

    broker = MagicMock()
    # Use British spelling — should still map to canonical 'canceled'
    broker.get_order.return_value = {"status": "cancelled"}

    reconcile_pending_orders(broker, recorder)

    row = recorder._conn.execute(
        "SELECT fill_status, fill_price FROM trades WHERE alpaca_order_id='ord_cxl_today'"
    ).fetchone()
    assert row["fill_status"] == "canceled"
    assert row["fill_price"] is None


def test_reconciler_continues_on_per_order_broker_failure(recorder):
    """One bad get_order call must not abort the loop."""
    today = date.today().isoformat()
    _seed_pending_trade(recorder, order_id="ord_bad", submitted_at=f"{today}T10:00:00")
    _seed_pending_trade(recorder, order_id="ord_good", submitted_at=f"{today}T10:01:00")

    def get_order_side_effect(order_id):
        if order_id == "ord_bad":
            raise RuntimeError("broker is down")
        return {"status": "filled", "filled_avg_price": "1.50"}

    broker = MagicMock()
    broker.get_order.side_effect = get_order_side_effect

    reconcile_pending_orders(broker, recorder)

    # The good order still gets resolved despite the bad one's failure
    row = recorder._conn.execute(
        "SELECT fill_status FROM trades WHERE alpaca_order_id='ord_good'"
    ).fetchone()
    assert row["fill_status"] == "filled"

    # The bad order stays pending
    row = recorder._conn.execute(
        "SELECT fill_status FROM trades WHERE alpaca_order_id='ord_bad'"
    ).fetchone()
    assert row["fill_status"] == "pending"
