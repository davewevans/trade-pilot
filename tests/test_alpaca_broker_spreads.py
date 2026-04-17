"""Tests for multi-leg order support in AlpacaBroker.

All Alpaca API calls are mocked — no real network calls.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, OptionLegRequest


# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def broker():
    """Return an AlpacaBroker with mocked API clients."""
    with patch("brokers.alpaca_broker.TradingClient") as MockTrading, \
         patch("brokers.alpaca_broker.OptionHistoricalDataClient") as MockData:
        from brokers.alpaca_broker import AlpacaBroker

        b = AlpacaBroker()
        b.client = MockTrading.return_value
        b.data_client = MockData.return_value
        return b


def _make_order_response(**overrides):
    """Create a mock order response with model_dump()."""
    defaults = {
        "id": "order-123",
        "status": "accepted",
        "legs": [],
        "order_class": "mleg",
    }
    defaults.update(overrides)
    mock = MagicMock()
    mock.model_dump.return_value = defaults
    return mock


# ── place_mleg_order ────────────────────────────────────────


class TestPlaceMlegOrder:
    def test_builds_correct_leg_requests(self, broker):
        broker.client.submit_order.return_value = _make_order_response()

        legs = [
            {"symbol": "SPY250502P00530000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": "SPY250502P00520000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]
        broker.place_mleg_order(legs=legs, limit_price=-1.80)

        call_args = broker.client.submit_order.call_args
        request = call_args[0][0]

        assert isinstance(request, LimitOrderRequest)
        assert request.order_class == OrderClass.MLEG
        assert request.limit_price == -1.80
        assert len(request.legs) == 2

        sell_leg = request.legs[0]
        assert sell_leg.symbol == "SPY250502P00530000"
        assert sell_leg.side == OrderSide.SELL
        assert sell_leg.position_intent == PositionIntent.SELL_TO_OPEN
        assert sell_leg.ratio_qty == 1

        buy_leg = request.legs[1]
        assert buy_leg.symbol == "SPY250502P00520000"
        assert buy_leg.side == OrderSide.BUY
        assert buy_leg.position_intent == PositionIntent.BUY_TO_OPEN

    def test_market_order_type(self, broker):
        broker.client.submit_order.return_value = _make_order_response()

        legs = [
            {"symbol": "SPY250502P00530000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": "SPY250502P00520000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]
        broker.place_mleg_order(legs=legs, order_type="market")

        request = broker.client.submit_order.call_args[0][0]
        assert isinstance(request, MarketOrderRequest)
        assert request.order_class == OrderClass.MLEG

    def test_returns_order_dict(self, broker):
        broker.client.submit_order.return_value = _make_order_response(id="abc-123")

        legs = [
            {"symbol": "SPY250502P00530000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": "SPY250502P00520000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]
        result = broker.place_mleg_order(legs=legs, limit_price=-1.50)
        assert result["id"] == "abc-123"
        assert result["order_class"] == "mleg"

    def test_invalid_order_type_raises(self, broker):
        with pytest.raises(ValueError, match="order_type"):
            broker.place_mleg_order(
                legs=[{"symbol": "X", "side": "buy", "ratio_qty": 1,
                       "position_intent": "buy_to_open"}],
                order_type="stop",
            )


class TestCreditDebitSignConvention:
    def test_credit_spread_positive_price_warns(self, broker, caplog):
        broker.client.submit_order.return_value = _make_order_response()

        legs = [
            {"symbol": "SPY250502P00530000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": "SPY250502P00520000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]
        with caplog.at_level(logging.WARNING, logger="brokers.alpaca_broker"):
            broker.place_mleg_order(legs=legs, limit_price=1.80)

        assert any("Credit spread but limit_price is positive" in m for m in caplog.messages)

    def test_credit_spread_negative_price_no_warning(self, broker, caplog):
        broker.client.submit_order.return_value = _make_order_response()

        legs = [
            {"symbol": "SPY250502P00530000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": "SPY250502P00520000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]
        with caplog.at_level(logging.WARNING, logger="brokers.alpaca_broker"):
            broker.place_mleg_order(legs=legs, limit_price=-1.80)

        assert not any("Credit spread" in m for m in caplog.messages)

    def test_debit_spread_negative_price_warns(self, broker, caplog):
        broker.client.submit_order.return_value = _make_order_response()

        # More buy legs than sell = debit spread
        legs = [
            {"symbol": "SPY250502C00540000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
            {"symbol": "SPY250502C00550000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": "SPY250502C00560000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]
        with caplog.at_level(logging.WARNING, logger="brokers.alpaca_broker"):
            broker.place_mleg_order(legs=legs, limit_price=-0.50)

        assert any("Debit spread but limit_price is negative" in m for m in caplog.messages)


# ── close_mleg_position ─────────────────────────────────────


class TestCloseMlegPosition:
    def test_reverses_buy_to_open(self, broker):
        broker.client.submit_order.return_value = _make_order_response()

        open_legs = [
            {"symbol": "SPY250502P00530000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": "SPY250502P00520000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]
        broker.close_mleg_position(open_legs, limit_price=0.30)

        request = broker.client.submit_order.call_args[0][0]
        legs = request.legs

        # sell_to_open → buy_to_close, side sell → buy
        assert legs[0].side == OrderSide.BUY
        assert legs[0].position_intent == PositionIntent.BUY_TO_CLOSE

        # buy_to_open → sell_to_close, side buy → sell
        assert legs[1].side == OrderSide.SELL
        assert legs[1].position_intent == PositionIntent.SELL_TO_CLOSE

    def test_reverses_close_to_open_edge_case(self, broker):
        broker.client.submit_order.return_value = _make_order_response()

        open_legs = [
            {"symbol": "SPY250502P00530000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_close"},
            {"symbol": "SPY250502P00520000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_close"},
        ]
        broker.close_mleg_position(open_legs, limit_price=0.10)

        request = broker.client.submit_order.call_args[0][0]
        # buy_to_close -> sell_to_open, side buy -> sell
        assert request.legs[0].side == OrderSide.SELL
        assert request.legs[0].position_intent == PositionIntent.SELL_TO_OPEN
        # sell_to_close -> buy_to_open, side sell -> buy
        assert request.legs[1].side == OrderSide.BUY
        assert request.legs[1].position_intent == PositionIntent.BUY_TO_OPEN

    def test_preserves_symbols(self, broker):
        broker.client.submit_order.return_value = _make_order_response()

        open_legs = [
            {"symbol": "AAPL250509C00185000", "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": "AAPL250509C00190000", "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]
        broker.close_mleg_position(open_legs, limit_price=0.20)

        request = broker.client.submit_order.call_args[0][0]
        symbols = [leg.symbol for leg in request.legs]
        assert symbols == ["AAPL250509C00185000", "AAPL250509C00190000"]


# ── get_option_snapshots ────────────────────────────────────


def _make_snapshot(bid=1.50, ask=1.60, delta=-0.30, theta=-0.05,
                   vega=0.08, gamma=0.02, iv=0.35, oi=5000, vol=1200):
    """Build a mock snapshot object with nested attributes."""
    quote = SimpleNamespace(bid_price=bid, ask_price=ask, bid_size=10, ask_size=10)
    greeks = SimpleNamespace(delta=delta, theta=theta, vega=vega, gamma=gamma, rho=0.01)
    latest_trade = SimpleNamespace(size=vol)
    return SimpleNamespace(
        latest_quote=quote,
        greeks=greeks,
        implied_volatility=iv,
        open_interest=oi,
        latest_trade=latest_trade,
    )


class TestGetOptionSnapshots:
    def test_returns_correct_structure(self, broker):
        snap = _make_snapshot(bid=2.10, ask=2.30, delta=-0.25, iv=0.42)
        broker.data_client.get_option_snapshot.return_value = {
            "SPY250502P00530000": snap,
        }

        result = broker.get_option_snapshots(["SPY250502P00530000"])

        assert "SPY250502P00530000" in result
        data = result["SPY250502P00530000"]
        assert data["bid"] == 2.10
        assert data["ask"] == 2.30
        assert data["mid"] == 2.20
        assert data["delta"] == -0.25
        assert data["iv"] == 0.42

    def test_missing_greeks_returns_none(self, broker):
        snap = SimpleNamespace(
            latest_quote=SimpleNamespace(bid_price=1.0, ask_price=1.2,
                                        bid_size=5, ask_size=5),
            greeks=None,
            implied_volatility=None,
            open_interest=None,
            latest_trade=None,
        )
        broker.data_client.get_option_snapshot.return_value = {
            "AAPL250509C00185000": snap,
        }

        result = broker.get_option_snapshots(["AAPL250509C00185000"])
        data = result["AAPL250509C00185000"]
        assert data["delta"] is None
        assert data["theta"] is None
        assert data["iv"] is None
        assert data["last_trade_size"] is None
        assert data["bid"] == 1.0

    def test_empty_symbols_returns_empty(self, broker):
        result = broker.get_option_snapshots([])
        assert result == {}

    def test_api_failure_returns_empty(self, broker):
        broker.data_client.get_option_snapshot.side_effect = Exception("API error")
        result = broker.get_option_snapshots(["SPY250502P00530000"])
        assert result == {}

    def test_multiple_symbols(self, broker):
        broker.data_client.get_option_snapshot.return_value = {
            "SYM_A": _make_snapshot(bid=1.0, ask=1.2),
            "SYM_B": _make_snapshot(bid=3.0, ask=3.4),
        }

        result = broker.get_option_snapshots(["SYM_A", "SYM_B"])
        assert len(result) == 2
        assert result["SYM_A"]["bid"] == 1.0
        assert result["SYM_B"]["bid"] == 3.0

    def test_large_watchlist_is_batched(self, broker):
        """Passing >50 symbols triggers multiple get_option_snapshot calls."""
        symbols = [f"SYM_{i:03d}" for i in range(110)]

        # Return a snapshot for every symbol regardless of which batch is asked for.
        def _batch_response(request):
            return {sym: _make_snapshot(bid=float(i), ask=float(i) + 0.1)
                    for i, sym in enumerate(request.symbol_or_symbols)}

        broker.data_client.get_option_snapshot.side_effect = _batch_response

        result = broker.get_option_snapshots(symbols)

        # All 110 symbols should be present in the merged result.
        assert set(result.keys()) == set(symbols)
        # 110 symbols / batch-size 50 = 3 batches → 3 API calls.
        assert broker.data_client.get_option_snapshot.call_count == 3

    def test_partial_batch_failure_still_returns_successful_batches(self, broker):
        """If one batch raises, the other batch's results are still returned."""
        symbols = [f"SYM_{i:03d}" for i in range(60)]

        call_count = [0]

        def _flaky_response(request):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated 400 Bad Request")
            return {sym: _make_snapshot() for sym in request.symbol_or_symbols}

        broker.data_client.get_option_snapshot.side_effect = _flaky_response

        result = broker.get_option_snapshots(symbols)

        # First batch failed, second batch (symbols 50–59) should be present.
        second_batch_symbols = symbols[50:]
        for sym in second_batch_symbols:
            assert sym in result
        # First batch symbols should be absent (not partially filled with None sentinel).
        for sym in symbols[:50]:
            assert sym not in result


# ── get_option_chain_with_greeks ────────────────────────────


class TestGetOptionChainWithGreeks:
    def test_returns_filtered_contracts(self, broker):
        mock_contract = SimpleNamespace(
            symbol="SPY250502P00530000",
            strike_price=530.0,
            expiration_date="2025-05-02",
            type=SimpleNamespace(value="put"),
            open_interest=1500,
            close_price=3.20,
        )
        mock_response = SimpleNamespace(option_contracts=[mock_contract])
        broker.client.get_option_contracts.return_value = mock_response

        result = broker.get_option_chain_with_greeks(
            underlying_symbol="SPY",
            expiration_date_gte="2025-04-20",
            expiration_date_lte="2025-05-10",
            contract_type="put",
        )

        assert len(result) == 1
        assert result[0]["symbol"] == "SPY250502P00530000"
        assert result[0]["strike_price"] == 530.0
        assert result[0]["type"] == "put"

    def test_empty_chain(self, broker):
        mock_response = SimpleNamespace(option_contracts=[])
        broker.client.get_option_contracts.return_value = mock_response

        result = broker.get_option_chain_with_greeks(
            underlying_symbol="XYZ",
            expiration_date_gte="2025-04-20",
            expiration_date_lte="2025-05-10",
        )
        assert result == []
