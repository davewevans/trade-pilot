"""Regression tests for alpaca-py UUID order-id coercion.

alpaca-py 0.43.2 returns ``uuid.UUID`` for order ids. SQLite has no
UUID adapter, so binding one raises ``sqlite3.ProgrammingError``. These
tests pin both the boundary coercion (AlpacaBroker) and the defensive
coercion in the persistence repositories.

Refs: sentry trade-pilot-fastapi 7478653118, 7478653066
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from database.db import Database
from database.repositories.decisions import DecisionRepository
from database.repositories.shadow_execution import ShadowExecutionRepository
from database.repositories.trades import TradeRepository


# ── Repository fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def conn(tmp_path):
    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    yield db.get_connection()
    db.close()


def _trade(alpaca_order_id):
    return {
        "cycle_id": "cycle-abc",
        "alpaca_order_id": alpaca_order_id,
        "underlying": "VZ",
        "strategy_type": "wheel",
        "trade_type": "SELL_PUT",
        "symbol": "VZ260619P00040000",
        "limit_price": 1.25,
        "submitted_at": "2026-05-14T18:04:28",
    }


def _decision(alpaca_order_id):
    return {
        "timestamp": "2026-05-14T18:04:28",
        "strategy_type": "wheel",
        "underlying": "VZ",
        "action": "SELL_PUT",
        "alpaca_order_id": alpaca_order_id,
    }


# ── Repository coercion ───────────────────────────────────────────────────────


def test_trade_insert_coerces_uuid_order_id(conn):
    oid = uuid.UUID("c0702675-3b5d-4b37-9190-7cdce7499a16")
    repo = TradeRepository(conn)

    row_id = repo.insert(_trade(oid))

    stored = conn.execute(
        "SELECT alpaca_order_id FROM trades WHERE id = ?", (row_id,)
    ).fetchone()[0]
    assert stored == str(oid)
    assert isinstance(stored, str)


def test_shadow_execution_insert_coerces_uuid_order_id(conn):
    oid = uuid.UUID("c0702675-3b5d-4b37-9190-7cdce7499a16")
    repo = ShadowExecutionRepository(conn)
    parent_row = {
        "submitted_at": "2026-05-14T18:04:28",
        "submitted_at_et": "2026-05-14T14:04:28",
        "strategy_type": "wheel",
        "action": "sell_put",
        "underlying": "VZ",
        "alpaca_order_id": oid,
        "order_kind": "single_leg",
        "is_credit": 1,
        "net_limit_abs": 1.25,
        "t0_status": "captured",
        "t0_attempts": 1,
        "t0_class": "always_fillable",
        "t0_net_bid": 1.20,
        "t0_net_mid": 1.25,
        "t0_net_ask": 1.30,
        "t0_captured_at": "2026-05-14T18:04:28",
    }

    exec_id = repo.insert_submission(parent_row=parent_row, legs=[])

    stored = conn.execute(
        "SELECT alpaca_order_id FROM shadow_executions WHERE id = ?", (exec_id,)
    ).fetchone()[0]
    assert stored == str(oid)
    assert isinstance(stored, str)


def test_decision_insert_coerces_uuid_order_id(conn):
    oid = uuid.UUID("c0702675-3b5d-4b37-9190-7cdce7499a16")
    repo = DecisionRepository(conn)

    row_id = repo.insert(_decision(oid))

    stored = conn.execute(
        "SELECT alpaca_order_id FROM decisions WHERE id = ?", (row_id,)
    ).fetchone()[0]
    assert stored == str(oid)
    assert isinstance(stored, str)


def test_trade_insert_still_accepts_string_order_id(conn):
    """Pre-existing string callers must keep working unchanged."""
    repo = TradeRepository(conn)
    row_id = repo.insert(_trade("plain-string-order-id"))
    stored = conn.execute(
        "SELECT alpaca_order_id FROM trades WHERE id = ?", (row_id,)
    ).fetchone()[0]
    assert stored == "plain-string-order-id"


# ── Broker boundary coercion ──────────────────────────────────────────────────


@pytest.fixture
def broker():
    with patch("brokers.alpaca_broker.TradingClient") as MockTrading, \
         patch("brokers.alpaca_broker.OptionHistoricalDataClient"):
        from brokers.alpaca_broker import AlpacaBroker

        b = AlpacaBroker()
        b.client = MockTrading.return_value
        return b


def _order_response(order_id):
    mock = MagicMock()
    mock.model_dump.return_value = {
        "id": order_id,
        "status": "accepted",
        "symbol": "VZ260619P00040000",
        "side": "sell",
        "qty": 1,
        "legs": [],
    }
    return mock


def test_place_order_coerces_uuid_id_to_str(broker):
    oid = uuid.UUID("c0702675-3b5d-4b37-9190-7cdce7499a16")
    broker.client.submit_order.return_value = _order_response(oid)

    data = broker.place_order(
        symbol="VZ260619P00040000", qty=1, side="sell",
        order_type="limit", time_in_force="day", limit_price=1.25,
    )

    assert data["id"] == str(oid)
    assert isinstance(data["id"], str)


def test_place_mleg_order_coerces_uuid_id_to_str(broker):
    oid = uuid.UUID("c0702675-3b5d-4b37-9190-7cdce7499a16")
    broker.client.submit_order.return_value = _order_response(oid)

    legs = [
        {"symbol": "SPY250502P00530000", "side": "sell",
         "ratio_qty": 1, "position_intent": "sell_to_open"},
        {"symbol": "SPY250502P00520000", "side": "buy",
         "ratio_qty": 1, "position_intent": "buy_to_open"},
    ]
    data = broker.place_mleg_order(legs=legs, limit_price=-1.80)

    assert data["id"] == str(oid)
    assert isinstance(data["id"], str)


def test_place_order_handles_none_id(broker):
    """A None id must pass through without raising."""
    broker.client.submit_order.return_value = _order_response(None)
    data = broker.place_order(
        symbol="VZ260619P00040000", qty=1, side="sell",
        order_type="limit", time_in_force="day", limit_price=1.25,
    )
    assert data["id"] is None
