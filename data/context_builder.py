"""Assembles all data sources into a single context dict for Claude's prompt."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from brokers.base import BaseBroker
from config import settings
from data import market_data
from data.trade_journal import TradeJournal

logger = logging.getLogger(__name__)

_news_client = NewsClient(
    api_key=settings.ALPACA_API_KEY,
    secret_key=settings.ALPACA_SECRET_KEY,
)


def _fetch_news(symbol: str, limit: int = 5) -> list[dict]:
    """Fetch recent headlines from Alpaca News for a symbol."""
    request = NewsRequest(symbols=symbol, limit=limit)
    news_set = _news_client.get_news(request)
    articles = news_set.news if hasattr(news_set, "news") else []
    return [
        {
            "headline": a.headline,
            "source": a.source,
            "url": a.url,
            "created_at": str(a.created_at),
        }
        for a in articles
    ]


class ContextBuilder:
    """Assembles all market data into a single context dict for Claude."""

    def __init__(self, broker: BaseBroker, data_client=None, journal: TradeJournal | None = None):
        self.broker = broker
        self.data_client = data_client
        self.journal = journal or TradeJournal()

    def build(self, symbol: str, wheel_state: str) -> dict:
        """Assemble the full context for Claude's decision-making.

        Fetches all data sources in parallel where possible.
        Any individual failure is logged and included as None.
        """
        t0 = time.monotonic()

        context: dict = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "symbol": symbol,
            "wheel_state": wheel_state,
            "account": None,
            "positions": None,
            "open_orders": None,
            "technicals": None,
            "fundamentals": None,
            "macro": None,
            "option_chain": None,
            "news": None,
            "recent_trades": None,
        }

        # ── Parallel fetches ────────────────────────────────
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures["technicals"] = pool.submit(market_data.get_stock_technicals, symbol)
            futures["fundamentals"] = pool.submit(market_data.get_fundamentals, symbol)
            futures["vix"] = pool.submit(market_data.get_vix)
            futures["fear_greed"] = pool.submit(market_data.get_fear_greed_index)
            futures["risk_free_rate"] = pool.submit(market_data.get_risk_free_rate)
            futures["news"] = pool.submit(_fetch_news, symbol)

        results: dict = {}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception:
                logger.warning("Failed to fetch %s for %s", key, symbol, exc_info=True)
                results[key] = None

        context["technicals"] = results["technicals"]
        context["fundamentals"] = results["fundamentals"]
        context["news"] = results["news"]

        # ── Macro ───────────────────────────────────────────
        vix = results["vix"]
        fg = results["fear_greed"] or {}
        context["macro"] = {
            "vix": vix,
            "vix_regime": market_data.interpret_vix(vix) if vix is not None else None,
            "fear_greed_score": fg.get("score"),
            "fear_greed_rating": fg.get("rating"),
            "risk_free_rate": results["risk_free_rate"],
        }

        # ── Broker data (sequential — same client) ─────────
        context["account"] = self._fetch_account()
        context["positions"] = self._fetch_positions(symbol)
        context["open_orders"] = self._fetch_orders(symbol)

        # ── Option chain (depends on technicals for price) ──
        current_price = None
        if context["technicals"] and "current_price" in context["technicals"]:
            current_price = context["technicals"]["current_price"]
        context["option_chain"] = self._fetch_option_chain(
            symbol, wheel_state, current_price,
        )

        # ── Trade journal ───────────────────────────────────
        try:
            journal_text = self.journal.format_for_prompt(symbol)
            context["recent_trades"] = journal_text or None
        except Exception:
            logger.warning("Failed to fetch trade journal for %s", symbol, exc_info=True)

        elapsed = time.monotonic() - t0
        logger.info("Context build for %s completed in %.2fs", symbol, elapsed)
        return context

    # ── Private helpers ──────────────────────────────────────

    def _fetch_account(self) -> dict | None:
        try:
            acct = self.broker.get_account()
            return {
                "buying_power": float(acct.get("buying_power", 0)),
                "options_trading_level": acct.get("options_trading_level"),
                "portfolio_value": float(acct.get("portfolio_value", 0)),
            }
        except Exception:
            logger.warning("Failed to fetch account info", exc_info=True)
            return None

    def _fetch_positions(self, symbol: str) -> list[dict] | None:
        try:
            positions = self.broker.get_positions()
            return [
                p for p in positions
                if symbol.upper() in str(p.get("symbol", "")).upper()
            ]
        except Exception:
            logger.warning("Failed to fetch positions for %s", symbol, exc_info=True)
            return None

    def _fetch_orders(self, symbol: str) -> list[dict] | None:
        try:
            orders = self.broker.get_orders(status="open")
            return [
                o for o in orders
                if symbol.upper() in str(o.get("symbol", "")).upper()
            ]
        except Exception:
            logger.warning("Failed to fetch orders for %s", symbol, exc_info=True)
            return None

    def _fetch_option_chain(
        self, symbol: str, wheel_state: str, current_price: float | None,
    ) -> dict | None:
        try:
            if wheel_state in ("SHORT_PUT", "SHORT_CALL"):
                positions = self.broker.get_positions()
                relevant = [
                    p for p in positions
                    if symbol.upper() in str(p.get("symbol", "")).upper()
                ]
                if relevant:
                    syms = [p["symbol"] for p in relevant if p.get("symbol")]
                    return market_data.get_option_snapshot(syms)
                return None

            if current_price is None:
                return None

            if wheel_state == "LONG_STOCK":
                option_type = "call"
                strike_price = current_price
            else:  # IDLE
                option_type = "put"
                strike_price = current_price

            contracts = self.broker.get_option_contracts(
                underlying_symbol=symbol,
                option_type=option_type,
                strike_price=strike_price,
            )
            if not contracts:
                return None

            # Filter to 14-35 DTE
            today = datetime.now().date()
            filtered = []
            for c in contracts:
                try:
                    exp = datetime.strptime(c["expiration_date"], "%Y-%m-%d").date()
                    dte = (exp - today).days
                    if 14 <= dte <= 35:
                        c["dte"] = dte
                        filtered.append(c)
                except (KeyError, ValueError):
                    continue

            if not filtered:
                return {"contracts": [], "snapshots": {}}

            # Get snapshots for the first 10 contracts
            snap_symbols = [c["symbol"] for c in filtered[:10]]
            snapshots = market_data.get_option_snapshot(snap_symbols)
            return {"contracts": filtered, "snapshots": snapshots}

        except Exception:
            logger.warning(
                "Failed to fetch option chain for %s (state=%s)",
                symbol, wheel_state, exc_info=True,
            )
            return None

    # ── Logging ──────────────────────────────────────────────

    @staticmethod
    def summarize_for_log(context: dict) -> str:
        """Return a compact single-line summary for logging."""
        symbol = context.get("symbol", "???")
        state = context.get("wheel_state", "???")

        tech = context.get("technicals") or {}
        price = tech.get("current_price")
        price_str = f"${price:,.2f}" if price is not None else "N/A"

        fund = context.get("fundamentals") or {}
        dte = fund.get("days_to_earnings")
        dte_str = f"{dte}d" if dte is not None else "N/A"

        macro = context.get("macro") or {}
        vix = macro.get("vix")
        vix_regime = macro.get("vix_regime", "?")
        vix_str = f"{vix:.1f} ({vix_regime})" if vix is not None else "N/A"

        fg_score = macro.get("fear_greed_score")
        fg_rating = macro.get("fear_greed_rating", "?")
        fg_str = f"{fg_score:.0f} ({fg_rating})" if fg_score is not None else "N/A"

        rfr = macro.get("risk_free_rate")
        rfr_str = f"{rfr * 100:.2f}%" if rfr is not None else "N/A"

        return (
            f"{symbol} | {state} | Price: {price_str} | "
            f"DTE Earnings: {dte_str} | VIX: {vix_str} | "
            f"F&G: {fg_str} | RFR: {rfr_str}"
        )
