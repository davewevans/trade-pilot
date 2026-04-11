"""Assembles all data sources into a single context dict for Claude's prompt."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from brokers.base import BaseBroker
from config import settings
from data import market_data
from data.market_regime import RegimeStabilityFilter, derive_market_regime
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
        self._regime_filter = RegimeStabilityFilter()

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
            "volatility": None,
            "earnings": None,
            "ex_dividend": None,
        }

        # ── Parallel fetches ────────────────────────────────
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures["technicals"] = pool.submit(market_data.get_stock_technicals, symbol)
            futures["fundamentals"] = pool.submit(market_data.get_fundamentals, symbol)
            futures["vix"] = pool.submit(market_data.get_vix)
            futures["fear_greed"] = pool.submit(market_data.get_fear_greed_index)
            futures["risk_free_rate"] = pool.submit(market_data.get_risk_free_rate)
            futures["news"] = pool.submit(_fetch_news, symbol)
            futures["orats_summary"] = pool.submit(market_data.get_orats_summary, symbol)
            futures["earnings"] = pool.submit(market_data.get_earnings_calendar, symbol)
            futures["vix_term"] = pool.submit(market_data.get_vix_term_structure)
            futures["ex_dividend"] = pool.submit(market_data.get_ex_dividend_date, symbol)

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

        # ── ORATS volatility analytics ──────────────────────
        orats = results.get("orats_summary")
        if orats:
            from data.orats_client import ORATSClient
            iv_env = ORATSClient.classify_iv_environment(orats.get("iv_rank_1y"))
        else:
            iv_env = "UNKNOWN"

        context["volatility"] = {
            "iv_rank_1y": orats.get("iv_rank_1y") if orats else None,
            "iv_rank_1m": orats.get("iv_rank_1m") if orats else None,
            "iv_pct_1y": orats.get("iv_pct_1y") if orats else None,
            "iv_pct_1m": orats.get("iv_pct_1m") if orats else None,
            "iv_environment": iv_env,
            "atm_iv_m1": orats.get("atm_iv_m1") if orats else None,
            "atm_iv_m2": orats.get("atm_iv_m2") if orats else None,
            "atm_iv_m3": orats.get("atm_iv_m3") if orats else None,
            "atm_iv_m4": orats.get("atm_iv_m4") if orats else None,
            "term_structure_slope": orats.get("term_structure_slope") if orats else None,
            "skew_m1": orats.get("skew_m1") if orats else None,
            "skew_m2": orats.get("skew_m2") if orats else None,
            "implied_move_pct": orats.get("implied_move_pct") if orats else None,
            "forecast_move_pct": orats.get("forecast_move_pct") if orats else None,
            "orats_available": orats is not None,
        }
        # Backward compat: iv_rank at top level for existing prompts
        context["iv_rank"] = context["volatility"]["iv_rank_1y"]
        context["iv_environment"] = iv_env

        # ── Earnings (Finnhub primary, yfinance fallback) ───
        earnings_data = results.get("earnings") or {}
        context["earnings"] = {
            "next_earnings_date": earnings_data.get("next_earnings_date"),
            "days_to_earnings": earnings_data.get("days_to_earnings"),
            "eps_estimate": earnings_data.get("eps_estimate"),
            "revenue_estimate": earnings_data.get("revenue_estimate"),
            "source": earnings_data.get("source", "unavailable"),
        }

        # ── Ex-dividend (for bear call spread assignment risk) ──
        ex_div = results.get("ex_dividend") or {}
        context["ex_dividend"] = {
            "next_ex_dividend_date": ex_div.get("next_ex_dividend_date"),
            "days_to_ex_dividend": ex_div.get("days_to_ex_dividend"),
            "annual_dividend_yield": ex_div.get("annual_dividend_yield"),
        }

        # ── Macro ───────────────────────────────────────────
        vix = results["vix"]
        fg = results["fear_greed"] or {}
        vix_term = results.get("vix_term") or {}
        context["macro"] = {
            "vix": vix,
            "vix_regime": market_data.interpret_vix(vix) if vix is not None else None,
            "fear_greed_score": fg.get("score"),
            "fear_greed_rating": fg.get("rating"),
            "risk_free_rate": results["risk_free_rate"],
            "vix9d": vix_term.get("vix9d"),
            "vix3m": vix_term.get("vix3m"),
            "vix6m": vix_term.get("vix6m"),
            "vix_contango": vix_term.get("contango"),
            "vix9d_vs_spot": vix_term.get("vix9d_vs_spot"),
            "vix_term_slope": vix_term.get("term_slope_m1_m3"),
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

        # ── SPX technicals (for regime derivation) ─────────
        if symbol.upper() != "SPY":
            try:
                context["spx_technicals"] = market_data.get_stock_technicals("SPY")
            except Exception:
                logger.warning("Failed to fetch SPX technicals for regime", exc_info=True)

        # ── Market regime ──────────────────────────────────
        try:
            derive_market_regime(context)
            raw_regime = context.get("market_regime", "NEUTRAL")
            changed = self._regime_filter.record_reading(raw_regime)
            confirmed = self._regime_filter.get_confirmed_regime()
            stable = self._regime_filter.is_stable()

            context["raw_market_regime"] = raw_regime
            context["confirmed_market_regime"] = confirmed
            context["regime_stable"] = stable
            if not stable:
                context["regime_unstable"] = True
        except Exception:
            logger.warning("Failed to derive market regime", exc_info=True)
            context["confirmed_market_regime"] = "NEUTRAL"
            context["regime_stable"] = False

        # ── Spread candidates (regime-driven) ─────────────────
        try:
            current_price = None
            if context["technicals"] and "current_price" in context["technicals"]:
                current_price = context["technicals"]["current_price"]

            if current_price is not None:
                regime = context.get("confirmed_market_regime", "NEUTRAL")
                iv_env = context.get("iv_environment", "MODERATE")
                strategy_types = self._select_spread_strategies(regime, iv_env)

                if strategy_types:
                    all_candidates: dict[str, dict] = {}
                    for st in strategy_types:
                        try:
                            candidates = self.build_spread_candidates(
                                symbol, st, underlying_price=current_price,
                            )
                            all_candidates[st] = candidates
                        except Exception:
                            logger.warning(
                                "Failed to build %s candidates for %s",
                                st, symbol, exc_info=True,
                            )
                    if all_candidates:
                        context["spread_candidates"] = all_candidates
        except Exception:
            logger.warning("Failed to build spread candidates for %s", symbol, exc_info=True)

        elapsed = time.monotonic() - t0
        logger.info("Context build for %s completed in %.2fs", symbol, elapsed)
        return context

    # ── Spread candidate building ───────────────────────────

    @staticmethod
    def _select_spread_strategies(regime: str, iv_env: str) -> list[str]:
        """Return strategy types appropriate for the current regime + IV."""
        if regime == "CRASH":
            return []
        if regime == "BEAR":
            return ["bear_call_spread"]
        if regime == "EUPHORIA":
            return ["bull_put_spread"]
        if regime == "BULL" and iv_env == "LOW":
            return ["long_call_vertical"]
        if regime == "BULL":
            return ["bull_put_spread"]
        # NEUTRAL
        if iv_env == "HIGH":
            return ["iron_condor"]
        if iv_env in ("MODERATE", "HIGH"):
            return ["bull_put_spread", "iron_condor"]
        return ["bull_put_spread"]

    def build_spread_candidates(
        self,
        underlying_symbol: str,
        strategy_type: str,
        dte_min: int = 20,
        dte_max: int = 50,
        short_delta_min: float = 0.15,
        short_delta_max: float = 0.30,
        wing_width_strikes: int = 5,
        underlying_price: float | None = None,
    ) -> dict:
        """Build spread candidates for Claude to evaluate.

        Returns a dict with ``candidates``, ``best_candidate``, and
        (for iron condors) ``iron_condor_legs``.
        """
        today = datetime.now().date()
        gte = (today + timedelta(days=dte_min)).isoformat()
        lte = (today + timedelta(days=dte_max)).isoformat()

        if underlying_price is None:
            tech = market_data.get_stock_technicals(underlying_symbol)
            underlying_price = tech.get("current_price", 0)

        result: dict = {
            "underlying": underlying_symbol,
            "underlying_price": underlying_price,
            "strategy_type": strategy_type,
            "candidates": [],
            "best_candidate": None,
            "iron_condor_legs": None,
        }

        if strategy_type == "iron_condor":
            put_result = self._build_credit_spread_candidates(
                underlying_symbol, "put", gte, lte, today,
                short_delta_min, short_delta_max, wing_width_strikes,
                underlying_price,
            )
            call_result = self._build_credit_spread_candidates(
                underlying_symbol, "call", gte, lte, today,
                short_delta_min, short_delta_max, wing_width_strikes,
                underlying_price,
            )
            result["candidates"] = put_result + call_result

            # Combine best put + call spread per expiration
            best_put = self._pick_best(put_result)
            best_call = self._pick_best(call_result)
            if best_put and best_call:
                total_credit = best_put["net_credit"] + best_call["net_credit"]
                total_max_loss = max(best_put["max_loss"], best_call["max_loss"])
                result["iron_condor_legs"] = {
                    "put_spread": best_put,
                    "call_spread": best_call,
                    "total_credit": round(total_credit, 4),
                    "total_max_loss": round(total_max_loss, 2),
                }
            result["best_candidate"] = (
                result["iron_condor_legs"] if result["iron_condor_legs"] else None
            )

        elif strategy_type == "long_call_vertical":
            result["candidates"] = self._build_debit_spread_candidates(
                underlying_symbol, gte, lte, today,
                wing_width_strikes, underlying_price,
            )
            result["best_candidate"] = self._pick_best_debit(result["candidates"])

        else:
            # bull_put_spread or bear_call_spread
            option_type = "put" if strategy_type == "bull_put_spread" else "call"
            result["candidates"] = self._build_credit_spread_candidates(
                underlying_symbol, option_type, gte, lte, today,
                short_delta_min, short_delta_max, wing_width_strikes,
                underlying_price,
            )
            result["best_candidate"] = self._pick_best(result["candidates"])

        return result

    def _build_credit_spread_candidates(
        self,
        symbol: str,
        option_type: str,
        gte: str,
        lte: str,
        today,
        delta_min: float,
        delta_max: float,
        wing_width: int,
        underlying_price: float,
    ) -> list[dict]:
        """Build credit spread candidates (bull put or bear call)."""
        contracts = self.broker.get_option_chain_with_greeks(
            underlying_symbol=symbol,
            expiration_date_gte=gte,
            expiration_date_lte=lte,
            contract_type=option_type,
        )
        if not contracts:
            return []

        # Fetch snapshots for all contracts
        all_symbols = [c["symbol"] for c in contracts]
        snapshots = self.broker.get_option_snapshots(all_symbols)

        # Index contracts by (expiration, strike) for pairing
        by_exp_strike: dict[tuple[str, float], dict] = {}
        for c in contracts:
            snap = snapshots.get(c["symbol"], {})
            strike = float(c.get("strike_price", 0))
            by_exp_strike[(c["expiration_date"], strike)] = {
                **c,
                **snap,
                "strike": strike,
            }

        # Collect unique expirations and sorted strikes per expiration
        exp_strikes: dict[str, list[float]] = {}
        for (exp, strike) in by_exp_strike:
            exp_strikes.setdefault(exp, []).append(strike)
        for exp in exp_strikes:
            exp_strikes[exp].sort()

        candidates = []
        for exp, strikes in exp_strikes.items():
            try:
                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            except ValueError:
                continue

            for short_strike in strikes:
                short_data = by_exp_strike.get((exp, short_strike), {})
                delta = short_data.get("delta")
                if delta is None:
                    continue

                abs_delta = abs(delta)
                if not (delta_min <= abs_delta <= delta_max):
                    continue

                # Find the long leg
                if option_type == "put":
                    long_strike = short_strike - wing_width
                else:
                    long_strike = short_strike + wing_width

                long_data = by_exp_strike.get((exp, long_strike))
                if long_data is None:
                    continue

                short_mid = short_data.get("mid")
                long_mid = long_data.get("mid")
                if short_mid is None or long_mid is None or short_mid <= 0:
                    continue

                net_credit = round(short_mid - long_mid, 4)
                if net_credit <= 0:
                    continue

                short_bid = short_data.get("bid", 0) or 0
                short_ask = short_data.get("ask", 0) or 0
                spread_pct = (
                    ((short_ask - short_bid) / short_mid * 100)
                    if short_mid > 0 else 999
                )

                short_oi = short_data.get("open_interest") or 0
                long_oi = long_data.get("open_interest") or 0
                liquidity_ok = short_oi >= 100 and long_oi >= 100 and spread_pct < 20

                max_loss = round((wing_width * 100) - (net_credit * 100), 2)
                max_gain = round(net_credit * 100, 2)

                if option_type == "put":
                    break_even = round(short_strike - net_credit, 4)
                else:
                    break_even = round(short_strike + net_credit, 4)

                cw_ratio = round(net_credit / wing_width, 4) if wing_width else 0

                candidates.append({
                    "expiration": exp,
                    "dte": dte,
                    "short_leg": {
                        "symbol": short_data.get("symbol", ""),
                        "strike": short_strike,
                        "delta": delta,
                        "bid": short_bid,
                        "ask": short_ask,
                        "mid": short_mid,
                        "open_interest": short_oi,
                        "bid_ask_spread_pct": round(spread_pct, 2),
                    },
                    "long_leg": {
                        "symbol": long_data.get("symbol", ""),
                        "strike": long_strike,
                        "delta": long_data.get("delta"),
                        "bid": long_data.get("bid", 0) or 0,
                        "ask": long_data.get("ask", 0) or 0,
                        "mid": long_mid,
                        "open_interest": long_oi,
                    },
                    "net_credit": net_credit,
                    "max_loss": max_loss,
                    "max_gain": max_gain,
                    "break_even": break_even,
                    "credit_to_width_ratio": cw_ratio,
                    "liquidity_ok": liquidity_ok,
                })

        return candidates

    def _build_debit_spread_candidates(
        self,
        symbol: str,
        gte: str,
        lte: str,
        today,
        wing_width: int,
        underlying_price: float,
    ) -> list[dict]:
        """Build debit spread candidates (long call vertical)."""
        contracts = self.broker.get_option_chain_with_greeks(
            underlying_symbol=symbol,
            expiration_date_gte=gte,
            expiration_date_lte=lte,
            contract_type="call",
        )
        if not contracts:
            return []

        all_symbols = [c["symbol"] for c in contracts]
        snapshots = self.broker.get_option_snapshots(all_symbols)

        by_exp_strike: dict[tuple[str, float], dict] = {}
        for c in contracts:
            snap = snapshots.get(c["symbol"], {})
            strike = float(c.get("strike_price", 0))
            by_exp_strike[(c["expiration_date"], strike)] = {
                **c,
                **snap,
                "strike": strike,
            }

        exp_strikes: dict[str, list[float]] = {}
        for (exp, strike) in by_exp_strike:
            exp_strikes.setdefault(exp, []).append(strike)
        for exp in exp_strikes:
            exp_strikes[exp].sort()

        candidates = []
        for exp, strikes in exp_strikes.items():
            try:
                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            except ValueError:
                continue

            for long_strike in strikes:
                long_data = by_exp_strike.get((exp, long_strike), {})
                delta = long_data.get("delta")
                if delta is None:
                    continue

                # Long leg should be ATM/ITM (delta 0.45-0.60)
                if not (0.45 <= delta <= 0.60):
                    continue

                short_strike = long_strike + wing_width
                short_data = by_exp_strike.get((exp, short_strike))
                if short_data is None:
                    continue

                short_delta = short_data.get("delta")
                if short_delta is not None and not (0.25 <= short_delta <= 0.40):
                    continue

                long_mid = long_data.get("mid")
                short_mid = short_data.get("mid")
                if long_mid is None or short_mid is None or long_mid <= 0:
                    continue

                net_debit = round(long_mid - short_mid, 4)
                if net_debit <= 0:
                    continue

                long_oi = long_data.get("open_interest") or 0
                short_oi = short_data.get("open_interest") or 0

                long_bid = long_data.get("bid", 0) or 0
                long_ask = long_data.get("ask", 0) or 0
                long_spread_pct = (
                    ((long_ask - long_bid) / long_mid * 100)
                    if long_mid > 0 else 999
                )
                liquidity_ok = long_oi >= 100 and short_oi >= 100 and long_spread_pct < 20

                max_loss = round(net_debit * 100, 2)
                max_gain = round((wing_width - net_debit) * 100, 2)
                break_even = round(long_strike + net_debit, 4)

                candidates.append({
                    "expiration": exp,
                    "dte": dte,
                    "short_leg": {
                        "symbol": short_data.get("symbol", ""),
                        "strike": short_strike,
                        "delta": short_delta,
                        "bid": short_data.get("bid", 0) or 0,
                        "ask": short_data.get("ask", 0) or 0,
                        "mid": short_mid,
                        "open_interest": short_oi,
                        "bid_ask_spread_pct": 0,
                    },
                    "long_leg": {
                        "symbol": long_data.get("symbol", ""),
                        "strike": long_strike,
                        "delta": delta,
                        "bid": long_bid,
                        "ask": long_ask,
                        "mid": long_mid,
                        "open_interest": long_oi,
                    },
                    "net_debit": net_debit,
                    "max_loss": max_loss,
                    "max_gain": max_gain,
                    "break_even": break_even,
                    "credit_to_width_ratio": round(
                        (wing_width - net_debit) / wing_width, 4
                    ) if wing_width else 0,
                    "liquidity_ok": liquidity_ok,
                })

        return candidates

    @staticmethod
    def _pick_best(candidates: list[dict]) -> dict | None:
        """Pick the credit spread candidate with highest credit_to_width_ratio and good liquidity."""
        liquid = [c for c in candidates if c.get("liquidity_ok")]
        if not liquid:
            return None
        return max(liquid, key=lambda c: c.get("credit_to_width_ratio", 0))

    @staticmethod
    def _pick_best_debit(candidates: list[dict]) -> dict | None:
        """Pick the debit spread candidate with best risk/reward and good liquidity."""
        liquid = [c for c in candidates if c.get("liquidity_ok")]
        if not liquid:
            return None
        # Best = highest max_gain / max_loss ratio
        return max(
            liquid,
            key=lambda c: c["max_gain"] / c["max_loss"] if c.get("max_loss", 0) > 0 else 0,
        )

    def calculate_current_spread_value(
        self,
        short_symbol: str,
        long_symbol: str,
    ) -> dict:
        """Calculate current value and P&L for an open spread position."""
        result: dict = {
            "short_current_mid": None,
            "long_current_mid": None,
            "current_spread_value": None,
            "original_credit": None,
            "pnl_pct": None,
            "dte_remaining": None,
        }

        try:
            snapshots = self.broker.get_option_snapshots([short_symbol, long_symbol])
        except Exception:
            logger.warning("Failed to fetch spread snapshots", exc_info=True)
            return result

        short_snap = snapshots.get(short_symbol, {})
        long_snap = snapshots.get(long_symbol, {})

        short_mid = short_snap.get("mid")
        long_mid = long_snap.get("mid")
        result["short_current_mid"] = short_mid
        result["long_current_mid"] = long_mid

        if short_mid is not None and long_mid is not None:
            result["current_spread_value"] = round(short_mid - long_mid, 4)

        # Look up original credit from journal
        try:
            for sym in (short_symbol, long_symbol):
                entries = self.journal.get_open_positions(sym)
                for e in entries:
                    if e.get("limit_price"):
                        result["original_credit"] = float(e["limit_price"])
                        break
                if result["original_credit"]:
                    break
        except Exception:
            pass

        if result["original_credit"] and result["current_spread_value"] is not None:
            captured = result["original_credit"] - result["current_spread_value"]
            result["pnl_pct"] = round(
                (captured / result["original_credit"]) * 100, 2,
            )

        # DTE from OCC symbol
        try:
            for i, ch in enumerate(short_symbol):
                if ch.isdigit():
                    date_part = short_symbol[i:i + 6]
                    exp = datetime.strptime(date_part, "%y%m%d").date()
                    result["dte_remaining"] = (exp - datetime.now().date()).days
                    break
        except (ValueError, IndexError):
            pass

        return result

    # ── Support bounce detection ───────────────────────────

    @staticmethod
    def detect_support_bounce(
        underlying_symbol: str,
        lookback_days: int = 10,
    ) -> dict:
        """Detect a CAHOLD (Close Above High Of Low Day) support bounce.

        Returns a dict with ``cahold_detected``, the low-day date and high,
        the current close, and whether the stock is above its 50-day SMA.
        """
        result: dict = {
            "cahold_detected": False,
            "low_day_date": None,
            "low_day_high": None,
            "current_close": None,
            "above_50sma": False,
        }

        try:
            import yfinance as yf

            ticker = yf.Ticker(underlying_symbol)
            hist = ticker.history(period="3mo")
            if hist is None or len(hist) < lookback_days + 1:
                return result

            recent = hist.tail(lookback_days)
            low_idx = recent["Low"].idxmin()
            low_day_high = float(recent.loc[low_idx, "High"])
            current_close = float(hist["Close"].iloc[-1])

            sma_50 = float(hist["Close"].rolling(50).mean().iloc[-1])
            above_50sma = current_close > sma_50

            result["low_day_date"] = str(low_idx.date()) if hasattr(low_idx, "date") else str(low_idx)
            result["low_day_high"] = round(low_day_high, 2)
            result["current_close"] = round(current_close, 2)
            result["above_50sma"] = above_50sma
            result["cahold_detected"] = current_close > low_day_high and above_50sma
        except Exception:
            logger.warning(
                "Failed to detect support bounce for %s", underlying_symbol,
                exc_info=True,
            )

        return result

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
