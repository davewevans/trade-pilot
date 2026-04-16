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
from data.source_health import SourceHealth
from data.trade_journal import TradeJournal

logger = logging.getLogger(__name__)

_health = SourceHealth()

_SOURCE_MAP: dict[str, str] = {
    "technicals": "yfinance",
    "fundamentals": "yfinance",
    "vix": "yfinance",
    "fear_greed": "CNN Fear & Greed",
    "risk_free_rate": "FRED",
    "news": "Alpaca News",
    "orats_summary": "ORATS",
    "orats_cores": "ORATS",
    "orats_monies": "ORATS",
    "earnings_history": "Finnhub",
    "analyst_data": "Finnhub",
    "news_sentiment": "Finnhub",
    "earnings": "Finnhub",
    "vix_term": "yfinance",
    "ex_dividend": "yfinance",
}


def _record_health(source_name: str, success: bool, detail: str = "") -> None:
    """Record a health event and warn if a source is persistently failing."""
    _health.record(source_name, success, detail)
    if not success:
        entry = _health.get_all().get(source_name, {})
        failures = entry.get("consecutive_failures", 0)
        if failures >= 3:
            logger.warning(
                "DATA SOURCE DEGRADED: %s has failed %d consecutive times. "
                "Last error: %s",
                source_name,
                failures,
                entry.get("last_failure_reason", ""),
            )

_news_client = NewsClient(
    api_key=settings.ALPACA_PAPER1_API_KEY,
    secret_key=settings.ALPACA_PAPER1_SECRET_KEY,
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
            "performance_stats": None,
            "skip_history": None,
            "portfolio_patterns": None,
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
            futures["orats_cores"] = pool.submit(market_data.get_orats_cores, symbol)
            futures["orats_monies"] = pool.submit(market_data.get_orats_monies, symbol)
            futures["earnings_history"] = pool.submit(
                market_data.get_finnhub_earnings_history, symbol,
            )
            futures["analyst_data"] = pool.submit(
                market_data.get_finnhub_analyst_data, symbol,
            )
            futures["news_sentiment"] = pool.submit(
                market_data.get_finnhub_news_sentiment, symbol,
            )
            futures["earnings"] = pool.submit(market_data.get_earnings_calendar, symbol)
            futures["vix_term"] = pool.submit(market_data.get_vix_term_structure)
            futures["ex_dividend"] = pool.submit(market_data.get_ex_dividend_date, symbol)

        results: dict = {}
        for key, future in futures.items():
            source_name = _SOURCE_MAP.get(key, key)
            try:
                results[key] = future.result()
                success = results[key] is not None
                _record_health(source_name, success,
                               "" if success else f"{key} returned None")
            except Exception as e:
                logger.warning("Failed to fetch %s for %s", key, symbol, exc_info=True)
                results[key] = None
                _record_health(source_name, False, str(e)[:200])

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

        cores = results.get("orats_cores") or {}
        monies_rows = results.get("orats_monies") or []

        # ── Premium richness signal from implied vs forecast move ──
        # If implied_move > forecast_move, options are overpriced relative
        # to ORATS' model of actual expected movement → sellers have edge.
        implied_move = orats.get("implied_move_pct") if orats else None
        forecast_move = orats.get("forecast_move_pct") if orats else None
        premium_richness: float | None = None
        premium_richness_label: str | None = None
        if implied_move is not None and forecast_move is not None and implied_move != 0:
            premium_richness = round((implied_move - forecast_move) / implied_move, 4)
            if premium_richness > 0.10:
                premium_richness_label = "RICH"      # selling has positive edge
            elif premium_richness < -0.10:
                premium_richness_label = "CHEAP"     # buying has positive edge
            else:
                premium_richness_label = "FAIR"

        # ── IV forecast vs current: is IV over or under ORATS' 20d forecast? ──
        iv_overvalued, iv_overvalued_label = _compute_iv_overvalued_label(
            current_iv=orats.get("atm_iv_m1") if orats else None,
            iv_fcst=cores.get("or_iv_fcst_20d"),
        )

        # ── Contango health signal ────────────────────────────
        contango_label = _compute_contango_label(cores.get("contango"))

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
            "implied_move_pct": implied_move,
            "forecast_move_pct": forecast_move,
            # Premium richness: >0 means options are expensive (sellers have edge)
            "premium_richness": premium_richness,
            "premium_richness_label": premium_richness_label,
            # Expanded summary fields
            "ex_ern_iv_30d": orats.get("ex_ern_iv_30d") if orats else None,
            "contango": orats.get("contango") if orats else None,
            "skewing": orats.get("skewing") if orats else None,
            # /cores enrichment
            "iv_hv_ratio": cores.get("iv_hv_ratio"),
            "iv_hv_ratio_1y_avg": cores.get("iv_hv_ratio_1y_avg"),
            "vol_of_vol": cores.get("vol_of_vol"),
            "vol_of_vol_label": _classify_vol_of_vol(cores.get("vol_of_vol")),
            "skew_percentile": cores.get("skew_percentile"),
            "hv_20d": cores.get("hv_20d"),
            "hv_ex_earnings_20d": cores.get("hv_ex_earnings_20d"),
            "rip": cores.get("rip"),
            # Earnings volatility — how much the stock typically moves on
            # earnings vs what options are currently pricing for the next event.
            "historical_avg_earnings_move": cores.get("abs_avg_earnings_move"),
            "implied_earnings_move": cores.get("implied_earnings_move"),
            "earnings_iv_premium": _earnings_iv_premium(
                cores.get("implied_earnings_move"),
                cores.get("abs_avg_earnings_move"),
            ),
            "orats_available": orats is not None,
            # Monies: vol smile availability
            "monies_available": len(monies_rows) > 0,
            # ORATS forecasts — used to determine if IV is over/undervalued
            "iv_forecast_20d": cores.get("or_iv_fcst_20d"),
            "hv_forecast_20d": cores.get("or_fcst_20d"),
            "iv_forecast_infinite": cores.get("or_fcst_inf"),
            # Ex-earnings IV — "clean" IV with earnings effect removed
            "ex_earnings_iv_20d": cores.get("ex_ern_iv_20d"),
            "ex_earnings_iv_30d": cores.get("ex_ern_iv_30d"),
            # Skew forecasts — is the skew over or undervalued?
            "slope_current": cores.get("slope"),
            "slope_forecast": cores.get("slope_fcst"),
            "slope_forecast_infinite": cores.get("slope_inf"),
            # Contango — term structure health signal
            "contango_cores": cores.get("contango"),
            "contango_forecast": cores.get("contango_fcst"),
            # Forward ratios — extreme values foreshadow vol regime changes
            "fwd_ratio_20_30": cores.get("fwd_ratio_20_30"),
            "fwd_ratio_30_60": cores.get("fwd_ratio_30_60"),
            # Forecast accuracy
            "orats_confidence": cores.get("confidence"),
            # IV overvaluation signal
            "iv_overvalued": iv_overvalued,
            "iv_overvalued_label": iv_overvalued_label,
            # Contango health label
            "contango_label": contango_label,
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
        # ORATS /cores secondary earnings source (if available)
        cores_ern = cores.get("next_earnings_date") if cores else None
        if cores_ern and cores_ern != "0000-00-00":
            context["earnings"]["orats_next_earnings_date"] = cores_ern
            context["earnings"]["orats_days_to_next_earnings"] = cores.get(
                "days_to_next_earnings",
            )
            context["earnings"]["orats_implied_earnings_move"] = cores.get(
                "implied_earnings_move",
            )
            context["earnings"]["orats_abs_avg_earnings_move"] = cores.get(
                "abs_avg_earnings_move",
            )
            context["earnings"]["orats_source"] = "orats"

        # ── Analyst data (Finnhub) ──────────────────────────
        analyst = results.get("analyst_data") or {}
        context["analyst"] = {
            "earnings_history": results.get("earnings_history") or [],
            "recommendations": analyst.get("recommendation"),
            "price_target": analyst.get("price_target"),
            "recent_rating_changes": analyst.get("recent_rating_changes", []),
            "news_sentiment": results.get("news_sentiment"),
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

        # ── Wheel cost basis (LONG_STOCK / SHORT_CALL only) ─
        # Surfaced from wheel_state.json so Claude can enforce the
        # "CC strike must be above effective cost basis" rule.
        if wheel_state in ("LONG_STOCK", "SHORT_CALL"):
            try:
                from strategies.wheel_strategy import STATE_FILE
                import json as _json, os as _os
                if _os.path.exists(STATE_FILE):
                    with open(STATE_FILE, "r") as _f:
                        _wstate = _json.load(_f)
                    pos = (
                        next(
                            (
                                p for p in (context["positions"] or [])
                                if (p.get("symbol") or "").upper() == symbol.upper()
                            ),
                            None,
                        )
                        or {}
                    )
                    assignment_price = float(pos.get("avg_entry_price", 0) or 0)
                    context["wheel_cost_basis"] = {
                        "effective_cost_basis": _wstate.get("cost_basis"),
                        "assignment_price": assignment_price,
                        "total_premium_collected": float(
                            _wstate.get("total_premium_collected", 0) or 0
                        ),
                        "roll_count": int(_wstate.get("roll_count", 0) or 0),
                    }
            except Exception:
                logger.warning(
                    "Failed to surface wheel cost basis for %s", symbol, exc_info=True,
                )

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

        # Performance stats for Claude's self-awareness
        try:
            stats_str = self.journal.format_stats_for_prompt(symbol, days=30)
            context["performance_stats"] = stats_str if stats_str else None
        except Exception:
            logger.warning("Failed to build performance stats for %s", symbol, exc_info=True)
            context["performance_stats"] = None

        # Skip history for Claude's pattern awareness
        try:
            skip_history = self.journal.format_skip_history_for_prompt(symbol, days=30)
            context["skip_history"] = skip_history if skip_history else None
        except Exception:
            logger.warning("Failed to build skip history for %s", symbol, exc_info=True)
            context["skip_history"] = None

        # Portfolio-level pattern summary (written weekly)
        try:
            patterns = self._load_portfolio_patterns()
            context["portfolio_patterns"] = patterns if patterns else None
        except Exception:
            logger.warning("Failed to load portfolio patterns", exc_info=True)
            context["portfolio_patterns"] = None

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
                            # Enrich all candidates + best_candidate with EV scores.
                            # Merge skew_percentile (from /cores) into the summary
                            # dict so _enrich_with_ev can use it for put-selling bonus.
                            enriched_summary = {
                                **(orats or {}),
                                "skew_percentile": cores.get("skew_percentile"),
                            }
                            _enrich_with_ev(
                                candidates,
                                strategy_type=st,
                                orats_summary=enriched_summary,
                                monies_rows=monies_rows,
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

    def build_strangle_candidates(
        self,
        underlying_symbol: str,
        dte_min: int = 30,
        dte_max: int = 50,
        short_delta_min: float = 0.15,
        short_delta_max: float = 0.20,
        underlying_price: float | None = None,
    ) -> dict:
        """Find the best OTM put and call for a short strangle.

        Both legs are short (sold). Returns a dict with the best candidate
        or None when no qualifying pair is found.
        """
        from data.orats_client import ORATSClient
        today = datetime.now().date()
        gte = (today + timedelta(days=dte_min)).isoformat()
        lte = (today + timedelta(days=dte_max)).isoformat()

        if underlying_price is None:
            tech = market_data.get_stock_technicals(underlying_symbol)
            underlying_price = tech.get("current_price", 0)

        put_candidates = self._build_credit_spread_candidates(
            underlying_symbol, "put", gte, lte, today,
            short_delta_min, short_delta_max, wing_width=0,
            underlying_price=underlying_price,
        )
        call_candidates = self._build_credit_spread_candidates(
            underlying_symbol, "call", gte, lte, today,
            short_delta_min, short_delta_max, wing_width=0,
            underlying_price=underlying_price,
        )

        result: dict = {
            "underlying": underlying_symbol,
            "underlying_price": underlying_price,
            "strategy_type": "strangle",
            "candidates": [],
            "best_candidate": None,
        }

        # Pair best put with best call from same expiration
        # Group by expiration
        put_by_exp: dict[str, dict] = {}
        for c in put_candidates:
            exp = c.get("expiration", "")
            if exp not in put_by_exp or (c.get("net_credit", 0) > put_by_exp[exp].get("net_credit", 0)):
                put_by_exp[exp] = c

        call_by_exp: dict[str, dict] = {}
        for c in call_candidates:
            exp = c.get("expiration", "")
            if exp not in call_by_exp or (c.get("net_credit", 0) > call_by_exp[exp].get("net_credit", 0)):
                call_by_exp[exp] = c

        strangle_candidates = []
        for exp in set(put_by_exp) & set(call_by_exp):
            put_leg = put_by_exp[exp]
            call_leg = call_by_exp[exp]
            total_credit = round(
                put_leg.get("net_credit", 0) + call_leg.get("net_credit", 0), 4
            )
            spread_yield = (
                round(total_credit / underlying_price, 6)
                if underlying_price and underlying_price > 0 else 0
            )
            liquidity_ok = (
                put_leg.get("short_leg", {}).get("open_interest", 0) >= 200
                and call_leg.get("short_leg", {}).get("open_interest", 0) >= 200
                and put_leg.get("short_leg", {}).get("bid_ask_spread_pct", 100) < 15
                and call_leg.get("short_leg", {}).get("bid_ask_spread_pct", 100) < 15
            )
            strangle_candidates.append({
                "expiration": exp,
                "dte": put_leg.get("dte"),
                "put_leg": put_leg.get("short_leg"),
                "call_leg": call_leg.get("short_leg"),
                "put_credit": put_leg.get("net_credit", 0),
                "call_credit": call_leg.get("net_credit", 0),
                "total_credit": total_credit,
                "spread_yield": spread_yield,
                "liquidity_ok": liquidity_ok,
            })

        result["candidates"] = strangle_candidates
        if strangle_candidates:
            result["best_candidate"] = max(
                strangle_candidates, key=lambda c: c.get("total_credit", 0)
            )

        return result

    def build_calendar_candidates(
        self,
        underlying_symbol: str,
        short_dte_min: int = 20,
        short_dte_max: int = 35,
        long_dte_min: int = 50,
        long_dte_max: int = 90,
        option_type: str = "call",
        underlying_price: float | None = None,
    ) -> dict:
        """Find the best ATM calendar spread (sell near-term, buy far-term).

        Both legs must have the same strike (ATM). Returns a dict with
        the best candidate or None when no qualifying pair is found.
        """
        today = datetime.now().date()

        if underlying_price is None:
            tech = market_data.get_stock_technicals(underlying_symbol)
            underlying_price = tech.get("current_price", 0)

        result: dict = {
            "underlying": underlying_symbol,
            "underlying_price": underlying_price,
            "strategy_type": "calendar_spread",
            "candidates": [],
            "best_candidate": None,
        }

        # Fetch near and far expiration chains
        short_gte = (today + timedelta(days=short_dte_min)).isoformat()
        short_lte = (today + timedelta(days=short_dte_max)).isoformat()
        long_gte = (today + timedelta(days=long_dte_min)).isoformat()
        long_lte = (today + timedelta(days=long_dte_max)).isoformat()

        # ATM options: delta 0.40–0.60
        short_contracts = self.broker.get_option_chain_with_greeks(
            underlying_symbol=underlying_symbol,
            expiration_date_gte=short_gte,
            expiration_date_lte=short_lte,
            contract_type=option_type,
        ) or []
        long_contracts = self.broker.get_option_chain_with_greeks(
            underlying_symbol=underlying_symbol,
            expiration_date_gte=long_gte,
            expiration_date_lte=long_lte,
            contract_type=option_type,
        ) or []

        if not short_contracts or not long_contracts:
            return result

        # Fetch snapshots
        all_syms = [c["symbol"] for c in short_contracts + long_contracts]
        snapshots = self.broker.get_option_snapshots(all_syms, underlying=underlying_symbol)

        # Index by (expiration, strike)
        def _index(contracts):
            idx = {}
            for c in contracts:
                snap = snapshots.get(c["symbol"], {})
                strike = float(c.get("strike_price", 0))
                idx[(c["expiration_date"], strike)] = {**c, **snap, "strike": strike}
            return idx

        short_idx = _index(short_contracts)
        long_idx = _index(long_contracts)

        # Find ATM strike (closest to underlying_price)
        all_strikes = set(s for (_, s) in list(short_idx.keys()) + list(long_idx.keys()))
        if not all_strikes:
            return result
        atm_strike = min(all_strikes, key=lambda s: abs(s - (underlying_price or 0)))

        # Pair short legs with long legs at the same ATM strike
        candidates = []
        short_exps = [exp for (exp, s) in short_idx if s == atm_strike]
        long_exps = [exp for (exp, s) in long_idx if s == atm_strike]

        for short_exp in short_exps:
            short_data = short_idx.get((short_exp, atm_strike))
            if not short_data:
                continue
            short_mid = short_data.get("mid")
            if not short_mid:
                continue

            for long_exp in long_exps:
                if long_exp <= short_exp:
                    continue
                long_data = long_idx.get((long_exp, atm_strike))
                if not long_data:
                    continue
                long_mid = long_data.get("mid")
                if not long_mid:
                    continue

                net_debit = round(long_mid - short_mid, 4)
                if net_debit <= 0 or net_debit > 2.50:
                    continue

                try:
                    short_dte = (datetime.strptime(short_exp, "%Y-%m-%d").date() - today).days
                    long_dte = (datetime.strptime(long_exp, "%Y-%m-%d").date() - today).days
                except ValueError:
                    continue

                if long_dte - short_dte < 30:
                    continue  # Expirations too close together

                short_oi = short_data.get("open_interest") or 0
                long_oi = long_data.get("open_interest") or 0
                liquidity_ok = short_oi >= 200 and long_oi >= 100

                candidates.append({
                    "short_expiration": short_exp,
                    "long_expiration": long_exp,
                    "strike": atm_strike,
                    "short_dte": short_dte,
                    "long_dte": long_dte,
                    "short_symbol": short_data.get("symbol", ""),
                    "long_symbol": long_data.get("symbol", ""),
                    "short_leg": {
                        "symbol": short_data.get("symbol", ""),
                        "strike": atm_strike,
                        "delta": short_data.get("delta"),
                        "mid": short_mid,
                        "open_interest": short_oi,
                    },
                    "long_leg": {
                        "symbol": long_data.get("symbol", ""),
                        "strike": atm_strike,
                        "delta": long_data.get("delta"),
                        "mid": long_mid,
                        "open_interest": long_oi,
                    },
                    "net_debit": net_debit,
                    "liquidity_ok": liquidity_ok,
                })

        result["candidates"] = candidates
        if candidates:
            result["best_candidate"] = min(candidates, key=lambda c: c.get("net_debit", 99))

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

        # Determine DTE range from gte/lte strings for ORATS query
        try:
            _today_dt = datetime.now().date()
            _dte_min = (datetime.strptime(gte, "%Y-%m-%d").date() - _today_dt).days
            _dte_max = (datetime.strptime(lte, "%Y-%m-%d").date() - _today_dt).days
        except (ValueError, AttributeError):
            _dte_min, _dte_max = 21, 45

        # Fetch snapshots preferring ORATS real-time greeks/quotes
        snapshots = self._enrich_with_orats_snapshots(
            contracts=contracts,
            symbol=symbol,
            option_type=option_type,
            dte_min=_dte_min,
            dte_max=_dte_max,
            delta_min=delta_min,
            delta_max=delta_max,
        )

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

                spread_yield = (
                    round(net_credit / underlying_price, 6)
                    if underlying_price and underlying_price > 0 else 0
                )
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
                    "spread_yield": spread_yield,
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

        # Determine DTE range from gte/lte for ORATS query
        try:
            _today_dt = datetime.now().date()
            _dte_min = (datetime.strptime(gte, "%Y-%m-%d").date() - _today_dt).days
            _dte_max = (datetime.strptime(lte, "%Y-%m-%d").date() - _today_dt).days
        except (ValueError, AttributeError):
            _dte_min, _dte_max = 21, 45

        # Use 0.15–0.65 call delta range to cover both long (0.45–0.60) and
        # short (0.25–0.40) legs; long legs below 0.15 fall back to Alpaca.
        snapshots = self._enrich_with_orats_snapshots(
            contracts=contracts,
            symbol=symbol,
            option_type="call",
            dte_min=_dte_min,
            dte_max=_dte_max,
            delta_min=0.15,
            delta_max=0.65,
        )

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

    def _enrich_with_orats_snapshots(
        self,
        contracts: list[dict],
        symbol: str,
        option_type: str,
        dte_min: int,
        dte_max: int,
        delta_min: float,
        delta_max: float,
    ) -> dict[str, dict]:
        """Return snapshots keyed by Alpaca OCC symbol, preferring ORATS data.

        For each contract, looks up (expiration_date, strike) in ORATS strikes
        data. Falls back to Alpaca get_option_snapshots() for any contract not
        covered by ORATS (e.g. long legs outside the delta range queried).

        Args:
            contracts: List of contract dicts from get_option_chain_with_greeks,
                each with ``symbol``, ``expiration_date``, and ``strike_price``.
            symbol: Underlying ticker (e.g. "SPY").
            option_type: "put" or "call".
            dte_min: Minimum DTE passed to ORATS strikes query.
            dte_max: Maximum DTE passed to ORATS strikes query.
            delta_min: Minimum absolute delta passed to ORATS strikes query.
            delta_max: Maximum absolute delta passed to ORATS strikes query.

        Returns:
            Dict keyed by Alpaca OCC symbol with snapshot fields
            (bid, ask, mid, delta, theta, vega, gamma, iv, open_interest, volume).
        """
        if not hasattr(self, '_orats_client'):
            from data.orats_client import ORATSClient
            self._orats_client = ORATSClient()

        orats_by_strike = self._orats_client.get_snapshots_by_strike(
            symbol=symbol,
            option_type=option_type,
            dte_min=dte_min,
            dte_max=dte_max,
            delta_min=delta_min,
            delta_max=delta_max,
        )

        result: dict[str, dict] = {}
        alpaca_fallback: list[str] = []

        for contract in contracts:
            occ_symbol = contract.get("symbol", "")
            exp_date = contract.get("expiration_date", "")
            try:
                strike = float(contract.get("strike_price", 0))
            except (TypeError, ValueError):
                strike = 0.0

            orats_snap = orats_by_strike.get((exp_date, strike))
            if orats_snap is not None:
                result[occ_symbol] = orats_snap
            else:
                alpaca_fallback.append(occ_symbol)

        if alpaca_fallback:
            logger.warning(
                "_enrich_with_orats_snapshots: %d contract(s) not in ORATS for %s %s "
                "(dte=%d-%d delta=%.2f-%.2f), falling back to Alpaca: %s",
                len(alpaca_fallback), symbol, option_type,
                dte_min, dte_max, delta_min, delta_max,
                alpaca_fallback[:5],
            )
            alpaca_snaps = self.broker.get_option_snapshots(alpaca_fallback, underlying=symbol)
            result.update(alpaca_snaps)

        return result

    @staticmethod
    def _pick_best(candidates: list[dict]) -> dict | None:
        """Pick the credit spread candidate with the highest EV score.

        Falls back to credit_to_width_ratio when EV scores are unavailable
        (e.g. ORATS monies data could not be fetched).
        """
        liquid = [c for c in candidates if c.get("liquidity_ok")]
        if not liquid:
            return None
        if any(c.get("ev_score") is not None for c in liquid):
            return max(liquid, key=lambda c: c.get("ev_score") or 0)
        return max(liquid, key=lambda c: c.get("credit_to_width_ratio", 0))

    @staticmethod
    def _pick_best_debit(candidates: list[dict]) -> dict | None:
        """Pick the debit spread candidate with the highest EV score.

        Falls back to max_gain/max_loss ratio when EV scores are unavailable.
        """
        liquid = [c for c in candidates if c.get("liquidity_ok")]
        if not liquid:
            return None
        if any(c.get("ev_score") is not None for c in liquid):
            return max(liquid, key=lambda c: c.get("ev_score") or 0)
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
            result = {
                "buying_power": float(acct.get("buying_power", 0)),
                "options_buying_power": float(acct.get("options_buying_power", 0)),
                "options_approved_level": acct.get("options_approved_level"),
                "options_trading_level": acct.get("options_trading_level"),
                "portfolio_value": float(acct.get("portfolio_value", 0)),
            }
            _record_health("Alpaca", True)
            return result
        except Exception as e:
            logger.warning("Failed to fetch account info", exc_info=True)
            _record_health("Alpaca", False, str(e)[:200])
            return None

    def _fetch_positions(self, symbol: str) -> list[dict] | None:
        try:
            if hasattr(self.broker, "get_all_positions"):
                positions = self.broker.get_all_positions()
            else:
                positions = self.broker.get_positions()
            _record_health("Alpaca", True)
            return [
                p for p in positions
                if symbol.upper() in str(p.get("symbol", "")).upper()
            ]
        except Exception as e:
            logger.warning("Failed to fetch positions for %s", symbol, exc_info=True)
            _record_health("Alpaca", False, str(e)[:200])
            return None

    def _fetch_orders(self, symbol: str) -> list[dict] | None:
        try:
            orders = self.broker.get_orders(status="open")
            _record_health("Alpaca", True)
            return [
                o for o in orders
                if symbol.upper() in str(o.get("symbol", "")).upper()
            ]
        except Exception as e:
            logger.warning("Failed to fetch orders for %s", symbol, exc_info=True)
            _record_health("Alpaca", False, str(e)[:200])
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
                return {"contracts": [], "snapshots": {}}

            if wheel_state == "LONG_STOCK":
                contract_type = "call"
                strike_gte = current_price * 1.01
                strike_lte = current_price * 1.20
                target_delta = 0.25
            else:  # IDLE
                contract_type = "put"
                strike_gte = current_price * 0.75
                strike_lte = current_price * 0.98
                target_delta = -0.25

            today = datetime.now().date()
            exp_gte = (today + timedelta(days=21)).isoformat()
            exp_lte = (today + timedelta(days=35)).isoformat()

            try:
                contracts = self.broker.get_option_chain_with_greeks(
                    underlying_symbol=symbol,
                    expiration_date_gte=exp_gte,
                    expiration_date_lte=exp_lte,
                    contract_type=contract_type,
                    strike_price_gte=f"{strike_gte:.2f}",
                    strike_price_lte=f"{strike_lte:.2f}",
                )
            except Exception:
                logger.warning(
                    "get_option_chain_with_greeks failed for %s (state=%s)",
                    symbol, wheel_state, exc_info=True,
                )
                return {"contracts": [], "snapshots": {}}

            if not contracts:
                return {"contracts": [], "snapshots": {}}

            # Prefer ORATS real-time greeks/quotes; fall back to Alpaca per-miss.
            # IDLE state: put delta 0.20–0.30; LONG_STOCK: call delta 0.20–0.35.
            _orats_option_type = contract_type  # "put" or "call"
            if contract_type == "put":
                _orats_delta_min, _orats_delta_max = 0.20, 0.30
            else:
                _orats_delta_min, _orats_delta_max = 0.20, 0.35

            snapshots = self._enrich_with_orats_snapshots(
                contracts=contracts,
                symbol=symbol,
                option_type=_orats_option_type,
                dte_min=21,
                dte_max=35,
                delta_min=_orats_delta_min,
                delta_max=_orats_delta_max,
            )

            enriched = []
            for c in contracts:
                snap = snapshots.get(c.get("symbol", ""), {})
                delta = snap.get("delta")
                if delta is None:
                    continue
                oi = c.get("open_interest") or snap.get("open_interest") or 0
                try:
                    if int(oi) < 100:
                        continue
                except (TypeError, ValueError):
                    continue
                try:
                    exp = datetime.strptime(
                        c["expiration_date"], "%Y-%m-%d",
                    ).date()
                    c["dte"] = (exp - today).days
                except (KeyError, ValueError):
                    pass
                c["delta"] = delta
                enriched.append(c)

            enriched.sort(key=lambda x: abs(x["delta"] - target_delta))
            top = enriched[:15]
            top_snapshots = {
                c["symbol"]: snapshots.get(c["symbol"], {}) for c in top
            }
            _record_health("Alpaca", True)
            return {"contracts": top, "snapshots": top_snapshots}

        except Exception as e:
            logger.warning(
                "Failed to fetch option chain for %s (state=%s)",
                symbol, wheel_state, exc_info=True,
            )
            _record_health("Alpaca", False, str(e)[:200])
            return {"contracts": [], "snapshots": {}}

    @staticmethod
    def _load_portfolio_patterns() -> str:
        """Load the portfolio pattern summary written by the weekly job.

        Returns a formatted string for Claude, or empty string if not yet
        available (first week of operation).
        """
        import json
        from config import settings

        path = settings.SNAPSHOTS_DIR / "portfolio_patterns.json"
        if not path.exists():
            return ""

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""

        days = data.get("lookback_days", 30)
        win_rate = data.get("win_rate")
        total_pnl = data.get("total_pnl", 0)
        assignment_rate = data.get("assignment_rate")
        win_ivr = data.get("avg_iv_rank_winning_trades")
        loss_ivr = data.get("avg_iv_rank_losing_trades")
        top_skips = data.get("top_skip_reasons", {})
        by_regime = data.get("performance_by_regime", {})

        lines = [f"Portfolio patterns (last {days} days, as of {data.get('generated_at', '?')}):"]

        if win_rate is not None:
            sign = "+" if total_pnl >= 0 else ""
            lines.append(
                f"  Overall: {win_rate}% win rate | "
                f"P&L: {sign}${total_pnl} | "
                f"{data.get('total_trades', 0)} trades, "
                f"{data.get('total_skips', 0)} skips"
            )

        if assignment_rate is not None:
            lines.append(f"  Assignment rate on CSPs: {assignment_rate}%")

        if win_ivr is not None and loss_ivr is not None:
            lines.append(
                f"  Avg IV rank: winning trades={win_ivr}, losing trades={loss_ivr}"
            )

        if top_skips:
            top = list(top_skips.items())[:3]
            skip_str = " | ".join(f"{r} (x{c})" for r, c in top)
            lines.append(f"  Top skip reasons: {skip_str}")

        if by_regime:
            regime_parts = []
            for regime, counts in by_regime.items():
                w = counts.get("wins", 0)
                l = counts.get("losses", 0)
                total = w + l
                if total > 0:
                    regime_parts.append(f"{regime}: {w}W/{l}L")
            if regime_parts:
                lines.append(f"  By regime: {' | '.join(regime_parts)}")

        body = "\n".join(lines)
        return f"<portfolio_patterns>\n{body}\n</portfolio_patterns>"

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


# ── Earnings volatility helpers ───────────────────────────────────────────────


def _classify_vol_of_vol(vov: float | None) -> str | None:
    """Classify ORATS volOfVol into HIGH / NORMAL / LOW.

    ORATS ``volOfVol`` measures how much implied volatility itself moves day
    to day (expressed as a fraction of ATM IV).  Typical equity range is
    0.05–0.35.  Thresholds below are heuristic — ORATS doesn't publish a
    per-symbol percentile for this field.

    HIGH  (> 0.30): IV is whipping around — mid-prices are unreliable and
                    profit targets can appear and vanish intraday.
    LOW   (< 0.10): IV is unusually stable — mid-prices are reliable and
                    profit targets are sticky once hit.
    NORMAL: standard operating range.
    """
    if vov is None:
        return None
    if vov > 0.30:
        return "HIGH"
    if vov < 0.10:
        return "LOW"
    return "NORMAL"


def _earnings_iv_premium(
    implied: float | None,
    historical: float | None,
) -> float | None:
    """Return (implied_earnings_move - historical_avg) / historical_avg.

    Positive → the market is pricing a bigger move than usual (fear/uncertainty
    premium).  Negative → the market is complacent relative to history.
    Returns None when either input is missing or historical is zero.
    """
    if implied is None or historical is None or historical == 0:
        return None
    return round((implied - historical) / historical, 4)


def _compute_iv_overvalued_label(
    current_iv: float | None,
    iv_fcst: float | None,
) -> tuple[float | None, str | None]:
    """Return (iv_overvalued_ratio, label) based on current vs ORATS forecast IV.

    iv_overvalued_ratio = (current_iv - iv_fcst) / current_iv
    - > 0.05  → OVERVALUED: current IV above forecast, good to sell
    - < -0.05 → UNDERVALUED: current IV below forecast, good to buy
    - else    → FAIR
    """
    if iv_fcst is None or current_iv is None or current_iv <= 0:
        return None, None
    ratio = round((current_iv - iv_fcst) / current_iv, 4)
    if ratio > 0.05:
        label = "OVERVALUED"
    elif ratio < -0.05:
        label = "UNDERVALUED"
    else:
        label = "FAIR"
    return ratio, label


def _compute_contango_label(contango_val: float | None) -> str | None:
    """Classify ORATS contango into NORMAL / FLAT / BACKWARDATION.

    - > 0.02  → NORMAL: short-term IV < long-term IV, healthy
    - > -0.02 → FLAT: term structure transitioning
    - else    → BACKWARDATION: short-term IV > long-term IV, bearish
    """
    if contango_val is None:
        return None
    if contango_val > 0.02:
        return "NORMAL"
    if contango_val > -0.02:
        return "FLAT"
    return "BACKWARDATION"


# ── EV / probability-of-profit enrichment ─────────────────────────────────────
# Module-level so it can be called from tests or other modules.


def compute_ev_score(
    candidate: dict,
    option_type: str,
    orats_summary: dict,
    monies_rows: list[dict],
) -> dict:
    """Compute EV-enriched probability of profit for a spread candidate.

    Uses ORATS smoothed volatility from the monies endpoint to calculate a
    more accurate probability than raw delta alone.  Also incorporates the
    gap between ORATS' implied move (market-priced) and its forecast move
    (model-estimated actual expected move) to detect premium richness/cheapness.

    Args:
        candidate: A spread candidate dict (with short_leg.delta, max_gain,
                   max_loss, dte fields).
        option_type: ``"put"`` for bull put/wheel CSP, ``"call"`` for bear
                     call, ``"debit"`` for long call vertical.
        orats_summary: Dict from ORATSClient.get_summary() with
                       implied_move_pct, forecast_move_pct, atm_iv_m1.
        monies_rows: List of rows from ORATSClient.get_monies() — one per
                     expiration.  May be empty if ORATS is unavailable.

    Returns:
        Dict with keys:
            base_pop           – naive 1 - |delta| probability
            skew_adjustment    – monies-derived adjustment for skew richness
            ev_adjustment      – implied-vs-forecast-move richness factor
            adjusted_pop       – final probability used for EV
            ev_score           – expected value in dollars (adjusted_pop *
                                 max_gain - (1-adjusted_pop) * max_loss)
            premium_richness   – raw (implied-forecast)/implied ratio
            monies_vol_at_strike – smoothed ORATS vol at the short strike delta
    """
    import math

    short_leg = candidate.get("short_leg") or {}
    short_delta = abs(short_leg.get("delta") or 0)
    max_gain = float(candidate.get("max_gain") or 0)
    max_loss = float(candidate.get("max_loss") or 1)
    dte = int(candidate.get("dte") or 30)

    # Guard: degenerate candidates
    if short_delta <= 0 or max_loss <= 0:
        return {
            "base_pop": None, "skew_adjustment": 0.0, "ev_adjustment": 0.0,
            "adjusted_pop": None, "ev_score": None,
            "premium_richness": None, "monies_vol_at_strike": None,
        }

    # ── 1. Base probability from delta ────────────────────────────────────────
    # For a short put/call: POP ≈ 1 - |delta|.
    # This is the risk-neutral probability the option expires OTM.
    base_pop = 1.0 - short_delta

    # ── 2. Smoothed vol at strike from ORATS monies ───────────────────────────
    # The monies endpoint returns vol at standardised delta-percentage levels:
    # vol100 ≈ ATM, vol30 = 30-delta put, vol5 = 5-delta put.
    # For a call spread: a 30-delta call = 70-delta put equivalent → vol70.
    monies_vol_at_strike: float | None = None
    atm_vol = orats_summary.get("atm_iv_m1")

    if monies_rows:
        # Match the monies row closest in DTE to the candidate expiration
        best_row: dict | None = None
        best_diff = float("inf")
        for row in monies_rows:
            exp_str = row.get("expir_date", "")
            try:
                from datetime import date as _date
                row_dte = (_date.fromisoformat(exp_str) - _date.today()).days
                diff = abs(row_dte - dte)
                if diff < best_diff:
                    best_diff = diff
                    best_row = row
            except (ValueError, TypeError):
                continue

        if best_row is not None:
            # Map candidate delta to the nearest monies bucket (multiples of 5)
            if option_type == "put":
                # Put side: vol30 for 30-delta put
                raw_level = short_delta * 100
            else:
                # Call side: a 30-delta call ≈ 70-delta put in put-skew space
                raw_level = (1.0 - short_delta) * 100

            bucket = int(round(raw_level / 5.0) * 5)
            bucket = max(5, min(95, bucket))
            field = f"vol{bucket}"
            monies_vol_at_strike = best_row.get(field)

    # ── 3. Skew adjustment ────────────────────────────────────────────────────
    # If the smoothed vol at the short strike is higher than ATM vol, the
    # market is paying extra for protection at that strike.  For put sellers
    # this is a tail-wind: the real-world probability of the put expiring
    # worthless is higher than the risk-neutral delta implies.
    skew_adjustment = 0.0
    if monies_vol_at_strike is not None and atm_vol and atm_vol > 0:
        skew_premium = (monies_vol_at_strike - atm_vol) / atm_vol
        # Apply a dampened adjustment: ±5% max from skew alone.
        if option_type in ("put",):
            # Put skew is positive → higher vol at OTM puts → sellers favoured
            skew_adjustment = min(0.05, max(-0.05, skew_premium * 0.10))
        elif option_type == "call":
            # OTM call vol is usually lower (or equal) — flip sign
            skew_adjustment = min(0.05, max(-0.05, -skew_premium * 0.10))
        # For debit spreads (buying calls) high call vol hurts us
        elif option_type == "debit":
            skew_adjustment = min(0.05, max(-0.05, -skew_premium * 0.10))

    # ── 4. Implied vs forecast move adjustment (user's formula) ───────────────
    # ev_adjustment = (implied_move - forecast_move) / implied_move
    # positive → options are overpriced → selling has extra edge
    # negative → options are underpriced → selling has less edge
    implied_move = orats_summary.get("implied_move_pct")
    forecast_move = orats_summary.get("forecast_move_pct")
    ev_adjustment = 0.0
    premium_richness: float | None = None

    if implied_move and forecast_move and implied_move != 0:
        ev_adjustment = (implied_move - forecast_move) / implied_move
        premium_richness = round(ev_adjustment, 4)
        # For debit spreads, overpriced options hurt buyers
        if option_type == "debit":
            ev_adjustment = -ev_adjustment

    # ── 5. Adjusted POP (user's formula) ──────────────────────────────────────
    adjusted_pop = base_pop * (1 + ev_adjustment * 0.10)
    adjusted_pop += skew_adjustment
    # Clamp: never go below 50% or above 99%
    adjusted_pop = max(0.50, min(0.99, adjusted_pop))

    # ── 6. Expected Value ──────────────────────────────────────────────────────
    ev_score = (adjusted_pop * max_gain) - ((1.0 - adjusted_pop) * max_loss)

    return {
        "base_pop": round(base_pop, 4),
        "skew_adjustment": round(skew_adjustment, 4),
        "ev_adjustment": round(ev_adjustment, 4),
        "adjusted_pop": round(adjusted_pop, 4),
        "ev_score": round(ev_score, 2),
        "premium_richness": premium_richness,
        "monies_vol_at_strike": monies_vol_at_strike,
    }


def _skew_percentile_adj(skew_percentile: float | None, is_put_seller: bool) -> float:
    """Return an EV score additive bonus (in dollars) based on skew richness.

    For put-selling strategies, high skew_percentile means OTM puts are
    trading at unusually high vol relative to history → premium sellers get
    paid more than average.  For call-selling (bear call spread) the
    skew_percentile signal is irrelevant — it measures put skew, not call skew.

    Thresholds are conservative: a 0.10 bonus moves a 0.50-EV candidate to
    0.60, enough to prefer a high-skew symbol when two are otherwise equal.
    """
    if skew_percentile is None or not is_put_seller:
        return 0.0
    if skew_percentile >= 80:
        return 0.10   # puts very rich vs 1-year history — strong seller edge
    if skew_percentile >= 60:
        return 0.05   # puts elevated — modest seller edge
    if skew_percentile <= 20:
        return -0.03  # puts cheap — slight penalty (selling underpriced fear)
    return 0.0


def _enrich_with_ev(
    candidates_result: dict,
    strategy_type: str,
    orats_summary: dict,
    monies_rows: list[dict],
) -> None:
    """Mutate a ``build_spread_candidates`` result in-place: add EV fields.

    Enriches every candidate in ``candidates_result["candidates"]`` with the
    output of ``compute_ev_score()``, then applies a skew-percentile bonus for
    put-selling strategies, and re-selects ``best_candidate`` using the final
    EV score as the primary sort key.

    Iron condor candidates are enriched on each leg and the combined entry
    is updated with a ``total_ev_score`` that is the sum of both legs.
    """
    from data.context_builder import ContextBuilder  # avoid circular for _pick*

    raw_candidates: list[dict] = candidates_result.get("candidates", [])

    # Skew percentile bonus: pull from orats_summary (merged in at call site)
    skew_pct = orats_summary.get("skew_percentile")

    # Determine option_type for the EV computation
    if strategy_type == "bull_put_spread":
        otype = "put"
    elif strategy_type == "bear_call_spread":
        otype = "call"
    elif strategy_type == "long_call_vertical":
        otype = "debit"
    else:  # iron_condor — treat put/call sides separately below
        otype = "put"

    is_put_seller = strategy_type in ("bull_put_spread", "wheel_csp")
    skew_adj = _skew_percentile_adj(skew_pct, is_put_seller)

    for c in raw_candidates:
        ev = compute_ev_score(c, otype, orats_summary, monies_rows)
        c.update(ev)
        # Apply skew-percentile bonus on top of monies-derived EV score
        if skew_adj != 0.0 and c.get("ev_score") is not None:
            c["ev_score"] = round(c["ev_score"] + skew_adj, 2)
        c["skew_percentile_adj"] = round(skew_adj, 4)

    # Re-elect best_candidate using ev_score
    if strategy_type == "iron_condor":
        # Re-pick both legs independently and rebuild iron_condor_legs
        put_candidates = [c for c in raw_candidates if (c.get("short_leg") or {}).get("delta", 0) < 0]
        call_candidates = [c for c in raw_candidates if (c.get("short_leg") or {}).get("delta", 0) > 0]

        # Enrich call candidates with their own EV (no skew bonus on call side)
        put_skew_adj = _skew_percentile_adj(skew_pct, is_put_seller=True)
        for c in call_candidates:
            ev = compute_ev_score(c, "call", orats_summary, monies_rows)
            c.update(ev)
            c["skew_percentile_adj"] = 0.0  # call side unaffected by put skew

        # Apply put-side skew bonus (put_candidates already enriched in loop above)
        if put_skew_adj != 0.0:
            for c in put_candidates:
                if c.get("ev_score") is not None:
                    c["ev_score"] = round(c["ev_score"] + put_skew_adj, 2)
                c["skew_percentile_adj"] = round(put_skew_adj, 4)

        best_put = ContextBuilder._pick_best(put_candidates)
        best_call = ContextBuilder._pick_best(call_candidates)

        if best_put and best_call:
            total_credit = best_put["net_credit"] + best_call["net_credit"]
            total_max_loss = max(best_put["max_loss"], best_call["max_loss"])
            put_ev = best_put.get("ev_score") or 0
            call_ev = best_call.get("ev_score") or 0
            candidates_result["iron_condor_legs"] = {
                "put_spread": best_put,
                "call_spread": best_call,
                "total_credit": round(total_credit, 4),
                "total_max_loss": round(total_max_loss, 2),
                "total_ev_score": round(put_ev + call_ev, 2),
            }
            candidates_result["best_candidate"] = candidates_result["iron_condor_legs"]
        else:
            candidates_result["best_candidate"] = None

    elif strategy_type == "long_call_vertical":
        candidates_result["best_candidate"] = ContextBuilder._pick_best_debit(raw_candidates)
    else:
        candidates_result["best_candidate"] = ContextBuilder._pick_best(raw_candidates)
