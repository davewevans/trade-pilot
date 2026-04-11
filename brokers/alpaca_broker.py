"""Alpaca broker implementation using the alpaca-py SDK."""

import logging

import requests as http_requests
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetClass,
    ContractType,
    OrderSide,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    GetOptionContractsRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
)

from brokers.base import BaseBroker
from config import settings

logger = logging.getLogger(__name__)

_SIDE_MAP = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}
_TIF_MAP = {"day": TimeInForce.DAY, "gtc": TimeInForce.GTC}
_STATUS_MAP = {
    "open": QueryOrderStatus.OPEN,
    "closed": QueryOrderStatus.CLOSED,
    "all": QueryOrderStatus.ALL,
}


class AlpacaBroker(BaseBroker):
    """Broker implementation backed by the Alpaca Trading API."""

    def __init__(self):
        self.client = TradingClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_SECRET_KEY,
            paper=settings.ALPACA_PAPER,
        )

    # ── account ──────────────────────────────────────────────

    def get_account(self) -> dict:
        """Return account info including buying power and options trading level."""
        account = self.client.get_account()
        data = account.model_dump()
        logger.info(
            "Account options_approved_level=%s options_trading_level=%s",
            data.get("options_approved_level"),
            data.get("options_trading_level"),
        )
        return data

    # ── option contracts ─────────────────────────────────────

    def get_option_contracts(
        self,
        underlying_symbol: str,
        expiration_date: str | None = None,
        option_type: str | None = None,
        strike_price: float | None = None,
    ) -> list[dict]:
        """Fetch available option contracts for a given underlying symbol."""
        params: dict = {"underlying_symbols": [underlying_symbol]}

        if expiration_date is not None:
            params["expiration_date"] = expiration_date

        if option_type is not None:
            params["type"] = (
                ContractType.CALL if option_type.lower() == "call" else ContractType.PUT
            )

        if strike_price is not None:
            margin = strike_price * 0.05
            params["strike_price_gte"] = str(strike_price - margin)
            params["strike_price_lte"] = str(strike_price + margin)

        request = GetOptionContractsRequest(**params)
        response = self.client.get_option_contracts(request)

        contracts = response.option_contracts or []
        return [
            {
                "id": c.id,
                "symbol": c.symbol,
                "name": c.name,
                "expiration_date": str(c.expiration_date),
                "root_symbol": c.root_symbol,
                "type": c.type.value if c.type else None,
                "strike_price": c.strike_price,
                "close_price": c.close_price,
                "open_interest": c.open_interest,
                "tradable": c.tradable,
            }
            for c in contracts
        ]

    def get_option_contract(self, symbol_or_id: str) -> dict:
        """Fetch a single option contract by OCC symbol or contract ID."""
        contract = self.client.get_option_contract(symbol_or_id)
        return contract.model_dump()

    # ── orders ───────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str,
        time_in_force: str,
        limit_price: float | None = None,
    ) -> dict:
        """Place a single-leg options order."""
        if not isinstance(qty, int) or qty != int(qty):
            raise ValueError(f"qty must be a whole number, got {qty}")

        if time_in_force not in _TIF_MAP:
            raise ValueError(f"time_in_force must be 'day' or 'gtc', got '{time_in_force}'")

        common = {
            "symbol": symbol,
            "qty": qty,
            "side": _SIDE_MAP[side],
            "time_in_force": _TIF_MAP[time_in_force],
        }

        if order_type == "market":
            request = MarketOrderRequest(**common)
        elif order_type == "limit":
            request = LimitOrderRequest(limit_price=limit_price, **common)
        else:
            raise ValueError(f"order_type must be 'market' or 'limit', got '{order_type}'")

        order = self.client.submit_order(request)
        data = order.model_dump()
        logger.info(
            "Order submitted id=%s symbol=%s side=%s qty=%s status=%s",
            data.get("id"),
            data.get("symbol"),
            data.get("side"),
            data.get("qty"),
            data.get("status"),
        )
        return data

    def get_orders(self, status: str | None = "open", limit: int = 50) -> list[dict]:
        """Return open or recent orders, filtered to options only."""
        query_status = _STATUS_MAP.get(status, QueryOrderStatus.OPEN)
        request = GetOrdersRequest(status=query_status, limit=limit)
        orders = self.client.get_orders(request)
        return [
            o.model_dump()
            for o in orders
            if o.asset_class == AssetClass.US_OPTION
        ]

    def cancel_order(self, order_id: str) -> None:
        """Cancel an open order by ID."""
        self.client.cancel_order_by_id(order_id)

    # ── positions ────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Return all open option positions."""
        positions = self.client.get_all_positions()
        return [
            p.model_dump()
            for p in positions
            if p.asset_class == AssetClass.US_OPTION
        ]

    def get_position(self, symbol_or_id: str) -> dict:
        """Return a single open position."""
        position = self.client.get_open_position(symbol_or_id)
        return position.model_dump()

    # ── exercise ─────────────────────────────────────────────

    def exercise_option(self, symbol_or_contract_id: str) -> None:
        """Submit an exercise instruction for a held option contract."""
        self.client.exercise_options_position(symbol_or_contract_id)

    # ── clock ────────────────────────────────────────────────

    def get_clock(self) -> dict:
        """Return the current market clock from Alpaca."""
        clock = self.client.get_clock()
        return clock.model_dump()

    # ── account activities ───────────────────────────────────

    def get_account_activities(
        self, activity_type: str | None = None
    ) -> list[dict]:
        """Return account activities via the Alpaca REST API.

        The alpaca-py TradingClient does not expose account activities,
        so this falls back to a direct HTTP GET.
        """
        headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
        }

        url = f"{settings.ALPACA_TRADE_URL}/v2/account/activities"

        params: dict = {}
        if activity_type is not None:
            params["activity_type"] = activity_type
        else:
            params["activity_type"] = "OEXP,OASGN,OEXC"

        resp = http_requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()
