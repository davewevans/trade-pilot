"""Alpaca broker implementation using the alpaca-py SDK."""

import logging

from utils.retry import retry_on_transient

import requests as http_requests
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetClass,
    ContractType,
    OrderClass,
    OrderSide,
    PositionIntent,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    GetOptionContractsRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    OptionLegRequest,
)

from brokers._oi_cache import OpenInterestCache
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
_INTENT_MAP = {
    "buy_to_open": PositionIntent.BUY_TO_OPEN,
    "buy_to_close": PositionIntent.BUY_TO_CLOSE,
    "sell_to_open": PositionIntent.SELL_TO_OPEN,
    "sell_to_close": PositionIntent.SELL_TO_CLOSE,
}
_REVERSE_INTENT = {
    "buy_to_open": "sell_to_close",
    "sell_to_open": "buy_to_close",
    "buy_to_close": "sell_to_open",
    "sell_to_close": "buy_to_open",
}
_REVERSE_SIDE = {"buy": "sell", "sell": "buy"}


class AlpacaBroker(BaseBroker):
    """Broker implementation backed by the Alpaca Trading API."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool | None = None,
    ):
        _api_key = api_key or settings.ALPACA_PAPER1_API_KEY
        _secret_key = secret_key or settings.ALPACA_PAPER1_SECRET_KEY
        _paper = paper if paper is not None else settings.ALPACA_PAPER

        self._api_key = _api_key
        self._secret_key = _secret_key

        self.client = TradingClient(
            api_key=_api_key,
            secret_key=_secret_key,
            paper=_paper,
        )
        self.data_client = OptionHistoricalDataClient(
            api_key=_api_key,
            secret_key=_secret_key,
        )
        self._oi_cache = OpenInterestCache(self.client)
        logger.info("AlpacaBroker initialized: paper=%s", _paper)

    # ── account ──────────────────────────────────────────────

    @retry_on_transient()
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

    @retry_on_transient()
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

    @retry_on_transient()
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

    @retry_on_transient()
    def get_order(self, order_id: str) -> dict:
        """Fetch a single order by ID. Used to confirm fills."""
        order = self.client.get_order_by_id(order_id)
        return order.model_dump()

    # ── positions ────────────────────────────────────────────

    @retry_on_transient()
    def get_positions(self) -> list[dict]:
        """Return all open option positions."""
        positions = self.client.get_all_positions()
        return [
            p.model_dump()
            for p in positions
            if p.asset_class == AssetClass.US_OPTION
        ]

    @retry_on_transient()
    def get_equity_positions(self) -> list[dict]:
        """Return all open equity (stock) positions."""
        positions = self.client.get_all_positions()
        return [
            p.model_dump()
            for p in positions
            if p.asset_class == AssetClass.US_EQUITY
        ]

    @retry_on_transient()
    def get_all_positions(self) -> list[dict]:
        """Return all open positions (equity + options)."""
        positions = self.client.get_all_positions()
        return [
            p.model_dump()
            for p in positions
            if p.asset_class in (AssetClass.US_EQUITY, AssetClass.US_OPTION)
        ]

    def get_position(self, symbol_or_id: str) -> dict:
        """Return a single open position."""
        position = self.client.get_open_position(symbol_or_id)
        return position.model_dump()

    # ── exercise ─────────────────────────────────────────────

    def exercise_option(self, symbol_or_contract_id: str) -> None:
        """Submit an exercise instruction for a held option contract."""
        self.client.exercise_options_position(symbol_or_contract_id)

    # ── multi-leg orders ──────────────────────────────────────

    @retry_on_transient()
    def place_mleg_order(
        self,
        legs: list[dict],
        order_type: str = "limit",
        limit_price: float | None = None,
        qty: int = 1,
        time_in_force: str = "day",
    ) -> dict:
        """Place a multi-leg options order (spreads, condors, etc.).

        Each leg dict must contain:
            symbol, side, ratio_qty, position_intent

        Credit spreads require a **negative** limit_price (e.g. -1.80
        for a $1.80 credit).  Debit spreads require a **positive**
        limit_price.  A warning is logged if the sign looks wrong.
        """
        # Build OptionLegRequest objects
        leg_requests = []
        sell_to_open_count = 0
        buy_to_open_count = 0
        for leg in legs:
            intent_str = leg["position_intent"].lower()
            if intent_str == "sell_to_open":
                sell_to_open_count += 1
            elif intent_str == "buy_to_open":
                buy_to_open_count += 1

            leg_requests.append(OptionLegRequest(
                symbol=leg["symbol"],
                ratio_qty=leg.get("ratio_qty", 1),
                side=_SIDE_MAP[leg["side"].lower()],
                position_intent=_INTENT_MAP[intent_str],
            ))

        # Warn on likely sign errors
        # A spread is credit when sell_to_open legs >= buy_to_open legs
        is_credit = sell_to_open_count >= buy_to_open_count and sell_to_open_count > 0
        if limit_price is not None and order_type == "limit":
            if is_credit and limit_price > 0:
                logger.warning(
                    "Credit spread but limit_price is positive (%.2f). "
                    "Credit spreads should have a negative limit_price.",
                    limit_price,
                )
            elif not is_credit and limit_price < 0:
                logger.warning(
                    "Debit spread but limit_price is negative (%.2f). "
                    "Debit spreads should have a positive limit_price.",
                    limit_price,
                )

        common: dict = {
            "qty": qty,
            "time_in_force": _TIF_MAP[time_in_force],
            "order_class": OrderClass.MLEG,
            "legs": leg_requests,
        }

        if order_type == "market":
            request = MarketOrderRequest(**common)
        elif order_type == "limit":
            request = LimitOrderRequest(limit_price=limit_price, **common)
        else:
            raise ValueError(f"order_type must be 'market' or 'limit', got '{order_type}'")

        order = self.client.submit_order(request)
        data = order.model_dump()

        leg_summary = ", ".join(
            f"{l['side']} {l['symbol']}" for l in legs
        )
        logger.info(
            "MLEG order submitted id=%s legs=[%s] limit_price=%s qty=%s",
            data.get("id"), leg_summary, limit_price, qty,
        )
        return data

    @retry_on_transient()
    def close_mleg_position(
        self,
        open_legs: list[dict],
        order_type: str = "limit",
        limit_price: float | None = None,
        qty: int = 1,
    ) -> dict:
        """Close a multi-leg position by reversing all legs atomically.

        ``open_legs`` should have the **original** position_intents.
        This method automatically reverses them and flips the sides.
        """
        reversed_legs = []
        for leg in open_legs:
            original_intent = leg["position_intent"].lower()
            original_side = leg["side"].lower()
            reversed_legs.append({
                "symbol": leg["symbol"],
                "ratio_qty": leg.get("ratio_qty", 1),
                "side": _REVERSE_SIDE[original_side],
                "position_intent": _REVERSE_INTENT[original_intent],
            })

        return self.place_mleg_order(
            legs=reversed_legs,
            order_type=order_type,
            limit_price=limit_price,
            qty=qty,
        )

    # ── option chain with filters ───────────────────────────

    @retry_on_transient()
    def get_option_chain_with_greeks(
        self,
        underlying_symbol: str,
        expiration_date_gte: str,
        expiration_date_lte: str,
        contract_type: str | None = None,
        strike_price_gte: str | None = None,
        strike_price_lte: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Fetch option contracts filtered by expiry/strike range.

        Returns contract metadata (symbol, strike, expiry, type, OI).
        For Greeks and quotes, call :meth:`get_option_snapshots` separately.
        """
        params: dict = {
            "underlying_symbols": [underlying_symbol],
            "expiration_date_gte": expiration_date_gte,
            "expiration_date_lte": expiration_date_lte,
            "limit": limit,
        }

        if contract_type is not None:
            params["type"] = (
                ContractType.CALL if contract_type.lower() == "call"
                else ContractType.PUT
            )
        if strike_price_gte is not None:
            params["strike_price_gte"] = strike_price_gte
        if strike_price_lte is not None:
            params["strike_price_lte"] = strike_price_lte

        logger.info(
            "Option data feed: indicative (15-min delayed). "
            "Paper trading does not support SIP real-time option data."
        )

        # Paginate through the contract list. Alpaca caps each
        # response; we walk next_page_token until exhausted or until
        # we hit a hard safety cap.
        all_contracts: list = []
        next_page_token = None
        while True:
            if next_page_token:
                params["page_token"] = next_page_token
            request = GetOptionContractsRequest(**params)
            response = self.client.get_option_contracts(request)
            batch = response.option_contracts or []
            all_contracts.extend(batch)

            next_page_token = getattr(response, "next_page_token", None)
            if not next_page_token or len(batch) == 0:
                break
            if len(all_contracts) >= 500:
                logger.warning(
                    "Option chain for %s hit 500 contract cap — "
                    "consider narrowing strike range",
                    underlying_symbol,
                )
                break

        contracts = all_contracts

        return [
            {
                "symbol": c.symbol,
                "strike_price": c.strike_price,
                "expiration_date": str(c.expiration_date),
                "type": c.type.value if c.type else None,
                "open_interest": c.open_interest,
                "close_price": c.close_price,
            }
            for c in contracts
        ]

    # ── option snapshots (greeks + quotes) ──────────────────

    _SNAPSHOT_BATCH_SIZE = 50

    @retry_on_transient()
    def get_option_snapshots(self, symbols: list[str], underlying: str = "") -> dict[str, dict]:
        """Fetch real-time snapshots (bid/ask, Greeks, IV) for option symbols.

        Open interest is sourced from the per-underlying OI cache (backed by
        /v2/options/contracts) rather than the snapshot object, which does not
        carry OI.  Pass ``underlying`` to enable cache lookup; omit it (or pass
        an empty string) when OI is not needed (e.g. position management calls).

        Symbols are chunked into batches of 50 to avoid HTTP 400 errors caused
        by URL length limits when the watchlist is large.  A batch failure is
        logged and skipped so remaining batches still contribute results.

        Returns a dict keyed by symbol.  Missing Greeks are returned as None.
        Never raises — returns an empty dict for symbols that fail.
        """
        result: dict[str, dict] = {}
        if not symbols:
            return result

        oi_map = self._oi_cache.get_map(underlying) if underlying else {}

        batch_size = self._SNAPSHOT_BATCH_SIZE
        batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
        logger.info(
            "Fetching option snapshots: total_symbols=%d batch_count=%d",
            len(symbols), len(batches),
        )

        logger.debug(
            "Using option data feed: indicative "
            "(15-min delayed quotes for paper trading)"
        )

        for batch in batches:
            try:
                request = OptionSnapshotRequest(symbol_or_symbols=batch)
                snapshots = self.data_client.get_option_snapshot(request)
            except Exception:
                logger.exception(
                    "Failed to fetch option snapshots for batch of %d symbols", len(batch)
                )
                continue

            for sym, snap in snapshots.items():
                try:
                    quote = snap.latest_quote
                    greeks = snap.greeks
                    bid = float(quote.bid_price) if quote and quote.bid_price is not None else None
                    ask = float(quote.ask_price) if quote and quote.ask_price is not None else None

                    # OptionsSnapshot has no daily_bar — that field exists only
                    # on the stock Snapshot model. Use latest_trade.size as a
                    # weak liquidity hint (last-trade contract size).
                    trade = snap.latest_trade
                    result[sym] = {
                        "bid": bid,
                        "ask": ask,
                        "mid": round((bid + ask) / 2, 4) if bid is not None and ask is not None else None,
                        "delta": float(greeks.delta) if greeks and greeks.delta is not None else None,
                        "theta": float(greeks.theta) if greeks and greeks.theta is not None else None,
                        "vega": float(greeks.vega) if greeks and greeks.vega is not None else None,
                        "gamma": float(greeks.gamma) if greeks and greeks.gamma is not None else None,
                        "iv": float(snap.implied_volatility) if snap.implied_volatility is not None else None,
                        "open_interest": oi_map.get(sym),
                        "last_trade_size": int(trade.size) if trade and trade.size is not None else None,
                    }
                except Exception:
                    logger.warning("Failed to parse snapshot for %s", sym, exc_info=True)
                    result[sym] = {
                        "bid": None, "ask": None, "mid": None,
                        "delta": None, "theta": None, "vega": None,
                        "gamma": None, "iv": None,
                        "open_interest": None, "last_trade_size": None,
                    }

        return result

    # ── clock ────────────────────────────────────────────────

    @retry_on_transient()
    def get_clock(self) -> dict:
        """Return the current market clock from Alpaca."""
        clock = self.client.get_clock()
        return clock.model_dump()

    # ── account activities ───────────────────────────────────

    def get_account_activities(
        self,
        activity_types: list[str],
        after: str | None = None,
    ) -> list[dict]:
        """Fetch recent account activities filtered by type.

        Args:
            activity_types: Activity codes (e.g. ["OPASN", "OPEXP",
                "OPEXC", "OPTRD"]).
            after: ISO timestamp; only activities after this time are
                returned. Defaults to 48 hours ago.

        Returns:
            Combined list of activity dicts (newest first per type).
            Per-type failures are logged but do not raise.
        """
        from datetime import datetime, timedelta, timezone

        if after is None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
            after = cutoff.isoformat()

        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

        all_activities: list[dict] = []
        for act_type in activity_types:
            try:
                resp = http_requests.get(
                    f"{settings.ALPACA_TRADE_URL}/v2/account/activities/{act_type}",
                    headers=headers,
                    params={"after": after, "direction": "desc", "page_size": 20},
                    timeout=10,
                )
                resp.raise_for_status()
                all_activities.extend(resp.json() or [])
            except Exception:
                logger.warning(
                    "Failed to fetch activities type=%s", act_type, exc_info=True,
                )
        return all_activities

    def get_portfolio_history(
        self,
        period: str = "3M",
        timeframe: str = "1D",
    ) -> dict:
        """Return portfolio equity history via the Alpaca REST API.

        Args:
            period:    Duration — "1W", "1M", "3M", "6M", "1A", "all".
            timeframe: Resolution — "1Min", "5Min", "15Min", "1H", "1D".

        Returns:
            Raw Alpaca response dict with keys: timestamp, equity,
            profit_loss, profit_loss_pct, base_value, timeframe.
            Returns empty dict on failure.
        """
        headers = {
            "APCA-API-KEY-ID":     self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
        }
        url    = f"{settings.ALPACA_TRADE_URL}/v2/account/portfolio/history"
        params = {"period": period, "timeframe": timeframe,
                  "extended_hours": False}
        try:
            resp = http_requests.get(
                url, headers=headers, params=params, timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.exception("Failed to fetch portfolio history")
            return {}
