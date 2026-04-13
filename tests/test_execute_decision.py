"""Tests for main.execute_decision — roll and close actions."""

from unittest.mock import MagicMock

from main import execute_decision


def _mk_broker(place_returns=None):
    broker = MagicMock()
    if place_returns is None:
        place_returns = [{"id": "ord-new"}]
    broker.place_order.side_effect = list(place_returns)
    return broker


def test_close_buys_to_close():
    broker = _mk_broker([{"id": "ord-close"}])
    decision = {
        "action": "close",
        "symbol": "AAPL260515P00260000",
        "qty": 1,
        "order_type": "limit",
        "limit_price": 1.25,
    }
    result = execute_decision(broker, decision)
    assert result == {"id": "ord-close"}
    broker.place_order.assert_called_once()
    kwargs = broker.place_order.call_args.kwargs
    assert kwargs["side"] == "buy"
    assert kwargs["symbol"] == "AAPL260515P00260000"
    assert kwargs["qty"] == 1
    assert kwargs["limit_price"] == 1.25


def test_roll_closes_then_opens():
    broker = _mk_broker([{"id": "ord-close"}, {"id": "ord-open"}])
    decision = {
        "action": "roll",
        "existing_symbol": "AAPL260515P00260000",
        "symbol": "AAPL260619P00255000",
        "qty": 1,
        "order_type": "limit",
        "limit_price": 2.30,
    }
    result = execute_decision(broker, decision)
    assert result == {"id": "ord-open"}
    assert broker.place_order.call_count == 2
    first = broker.place_order.call_args_list[0].kwargs
    second = broker.place_order.call_args_list[1].kwargs
    assert first["side"] == "buy"
    assert first["symbol"] == "AAPL260515P00260000"
    assert second["side"] == "sell"
    assert second["symbol"] == "AAPL260619P00255000"


def test_roll_without_existing_symbol_aborts():
    broker = _mk_broker()
    decision = {
        "action": "roll",
        "symbol": "AAPL260619P00255000",
        "qty": 1,
        "order_type": "limit",
        "limit_price": 2.30,
    }
    result = execute_decision(broker, decision)
    assert result is None
    broker.place_order.assert_not_called()


def test_roll_aborts_if_close_fails():
    broker = MagicMock()
    # First call (close) returns None/no id — second call must not happen
    broker.place_order.return_value = None
    decision = {
        "action": "roll",
        "existing_symbol": "AAPL260515P00260000",
        "symbol": "AAPL260619P00255000",
        "qty": 1,
        "order_type": "limit",
        "limit_price": 2.30,
    }
    result = execute_decision(broker, decision)
    assert result is None
    assert broker.place_order.call_count == 1
