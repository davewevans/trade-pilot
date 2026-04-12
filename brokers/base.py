"""Abstract BaseBroker class defining the common broker interface."""

from abc import ABC, abstractmethod


class BaseBroker(ABC):
    """Abstract base class that all broker implementations must inherit from."""

    @abstractmethod
    def get_account(self) -> dict:
        """Return account info including buying power and options trading level."""

    @abstractmethod
    def get_option_contracts(
        self,
        underlying_symbol: str,
        expiration_date: str | None = None,
        option_type: str | None = None,
        strike_price: float | None = None,
    ) -> list[dict]:
        """Fetch available option contracts for a given underlying symbol.

        Args:
            underlying_symbol: The ticker symbol (e.g. "AAPL").
            expiration_date: Filter by expiration date (YYYY-MM-DD).
            option_type: Filter by "call" or "put".
            strike_price: Filter by exact strike price.
        """

    @abstractmethod
    def get_option_contract(self, symbol_or_id: str) -> dict:
        """Fetch a single option contract by OCC symbol or contract ID.

        Args:
            symbol_or_id: OCC symbol (e.g. AAPL240119C00190000) or contract ID.
        """

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str,
        time_in_force: str,
        limit_price: float | None = None,
    ) -> dict:
        """Place a single-leg options order.

        Args:
            symbol: The OCC option symbol.
            qty: Number of contracts.
            side: "buy" or "sell".
            order_type: "market" or "limit".
            time_in_force: "day" or "gtc".
            limit_price: Required for limit orders.
        """

    @abstractmethod
    def get_orders(self, status: str | None = None, limit: int = 50) -> list[dict]:
        """Return open or recent orders.

        Args:
            status: Filter by "open", "closed", or "all".
            limit: Maximum number of orders to return.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """Cancel an open order by ID.

        Args:
            order_id: The broker-assigned order ID.
        """

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return all open option positions (asset_class == "option")."""

    @abstractmethod
    def get_position(self, symbol_or_id: str) -> dict:
        """Return a single open position.

        Args:
            symbol_or_id: OCC symbol or position/contract ID.
        """

    @abstractmethod
    def exercise_option(self, symbol_or_contract_id: str) -> None:
        """Submit an exercise instruction for a held option contract.

        Corresponds to POST /v2/positions/{symbol_or_contract_id}/exercise.

        Args:
            symbol_or_contract_id: OCC symbol or contract ID of the held option.
        """

    @abstractmethod
    def get_clock(self) -> dict:
        """Return the current market clock.

        Returns a dict with at least:
            is_open (bool): Whether the market is currently open.
            next_open (str): ISO timestamp of next market open.
            next_close (str): ISO timestamp of next market close.
        """

    @abstractmethod
    def get_account_activities(
        self,
        activity_types: list[str],
        after: str | None = None,
    ) -> list[dict]:
        """Return account activities filtered by type.

        Args:
            activity_types: Activity codes (e.g. OPASN, OPEXP, OPEXC, OPTRD).
            after: ISO timestamp; defaults to 48 hours ago.
        """

    def get_order(self, order_id: str) -> dict:
        """Fetch a single order by ID. Optional — used to confirm fills."""
        raise NotImplementedError

    def get_portfolio_history(
        self, period: str = "3M", timeframe: str = "1D"
    ) -> dict:
        """Return portfolio history. Override in broker implementations."""
        return {}
