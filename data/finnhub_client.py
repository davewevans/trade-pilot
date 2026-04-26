"""Finnhub API client built on the official finnhub-python SDK.

Provides earnings calendar + history, analyst recommendations + price
targets + rating changes, and NLP news sentiment. Free tier:
60 requests/minute.
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

import finnhub

from config import settings

logger = logging.getLogger(__name__)

_EARNINGS_TTL = 6 * 3600
_EARNINGS_HISTORY_TTL = 24 * 3600
_ANALYST_TTL = 6 * 3600
_SENTIMENT_TTL = 1 * 3600
_PROFILE_TTL = 6 * 3600

_earnings_cache: dict[str, tuple[float, dict]] = {}
_earnings_history_cache: dict[str, tuple[float, list]] = {}
_analyst_cache: dict[str, tuple[float, dict]] = {}
_sentiment_cache: dict[str, tuple[float, dict]] = {}
_profile_cache: dict[str, tuple[float, dict]] = {}

# Endpoints that returned 403 once — disabled for the rest of the process.
# Only 403 sets the flag; other errors (rate limit, transient) still retry.
_ENDPOINT_DISABLED: dict[str, bool] = {
    "price_target": False,
    "upgrade_downgrade": False,
    "news_sentiment": False,
}


class FinnhubClient:
    """Client for the Finnhub API (wraps the official finnhub-python SDK)."""

    def __init__(self, api_key: str | None = None):
        key = api_key or settings.FINNHUB_API_KEY
        self._client = finnhub.Client(api_key=key)
        if not settings.FINNHUB_PAID_TIER:
            _ENDPOINT_DISABLED["price_target"] = True
            _ENDPOINT_DISABLED["upgrade_downgrade"] = True
            _ENDPOINT_DISABLED["news_sentiment"] = True

    # ── Earnings calendar (next upcoming) ───────────────────

    def get_next_earnings(self, symbol: str) -> Optional[dict]:
        """Return the next upcoming earnings date for a symbol.

        Returns ``{next_earnings_date, days_to_earnings, eps_estimate,
        revenue_estimate}`` or None on failure.  Cached for 6 hours.
        """
        cached = _earnings_cache.get(symbol)
        if cached:
            ts, data = cached
            if time.monotonic() - ts < _EARNINGS_TTL:
                return data

        try:
            today = date.today()
            payload = self._client.earnings_calendar(
                _from=str(today),
                to=str(today + timedelta(days=90)),
                symbol=symbol,
            )
            events = (payload or {}).get("earningsCalendar", []) or []

            future = []
            for e in events:
                try:
                    ed = date.fromisoformat(str(e.get("date", "")))
                    if ed >= today:
                        future.append((ed, e))
                except (ValueError, TypeError):
                    continue

            if not future:
                result = {
                    "next_earnings_date": None,
                    "days_to_earnings": None,
                    "eps_estimate": None,
                    "revenue_estimate": None,
                }
            else:
                future.sort(key=lambda x: x[0])
                ed, event = future[0]
                result = {
                    "next_earnings_date": str(ed),
                    "days_to_earnings": (ed - today).days,
                    "eps_estimate": event.get("epsEstimate"),
                    "revenue_estimate": event.get("revenueEstimate"),
                }

            _earnings_cache[symbol] = (time.monotonic(), result)
            logger.info(
                "Finnhub earnings for %s: date=%s days=%s",
                symbol, result["next_earnings_date"], result["days_to_earnings"],
            )
            return result

        except Exception:
            logger.warning("Finnhub earnings failed for %s", symbol, exc_info=True)
        return None

    # ── Earnings surprise history ───────────────────────────

    def get_earnings_history(self, symbol: str, limit: int = 8) -> list[dict]:
        """Return recent quarterly earnings surprises (most recent first).

        Filters out unconfirmed entries (actual is None). Cached for 24h.
        """
        cached = _earnings_history_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < _EARNINGS_HISTORY_TTL:
            return cached[1]

        try:
            rows = self._client.company_earnings(symbol, limit=limit) or []
        except Exception:
            logger.warning(
                "Finnhub company_earnings failed for %s", symbol, exc_info=True,
            )
            return []

        normalized: list[dict] = []
        for row in rows:
            if row.get("actual") is None:
                continue
            normalized.append({
                "period": row.get("period"),
                "actual": row.get("actual"),
                "estimate": row.get("estimate"),
                "surprise": row.get("surprise"),
                "surprise_pct": row.get("surprisePercent"),
            })

        normalized.sort(key=lambda r: r.get("period") or "", reverse=True)
        normalized = normalized[:limit]

        _earnings_history_cache[symbol] = (time.monotonic(), normalized)
        return normalized

    # ── Analyst data (recs + price target + rating changes) ─

    def get_analyst_data(self, symbol: str) -> dict:
        """Return analyst consensus, price target, and recent rating changes.

        Each sub-call is wrapped independently so a failure on one
        endpoint does not wipe out the others. Cached for 6 hours.
        """
        cached = _analyst_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < _ANALYST_TTL:
            return cached[1]

        recommendation: dict | None = None
        try:
            recs = self._client.recommendation_trends(symbol) or []
            if recs:
                top = recs[0]
                recommendation = {
                    "strong_buy": top.get("strongBuy"),
                    "buy": top.get("buy"),
                    "hold": top.get("hold"),
                    "sell": top.get("sell"),
                    "strong_sell": top.get("strongSell"),
                    "period": top.get("period"),
                }
        except Exception:
            logger.warning(
                "Finnhub recommendation_trends failed for %s", symbol, exc_info=True,
            )

        price_target: dict = {
            "mean": None, "high": None, "low": None, "median": None,
        }
        if not _ENDPOINT_DISABLED["price_target"]:
            try:
                pt = self._client.price_target(symbol) or {}
                price_target = {
                    "mean": pt.get("targetMean"),
                    "high": pt.get("targetHigh"),
                    "low": pt.get("targetLow"),
                    "median": pt.get("targetMedian"),
                }
            except finnhub.FinnhubAPIException as e:
                if getattr(e, "status_code", None) == 403:
                    if not _ENDPOINT_DISABLED["price_target"]:
                        logger.warning(
                            "finnhub_endpoint_disabled_403",
                            extra={"endpoint": "price_target",
                                   "detail": "Tier does not include this endpoint; disabling for session."},
                        )
                        _ENDPOINT_DISABLED["price_target"] = True
                else:
                    logger.warning("Finnhub price_target failed for %s", symbol, exc_info=True)
            except Exception:
                logger.warning("Finnhub price_target failed for %s", symbol, exc_info=True)

        recent_changes: list[dict] = []
        if not _ENDPOINT_DISABLED["upgrade_downgrade"]:
            try:
                changes = self._client.upgrade_downgrade(symbol=symbol) or []
                for ch in changes[:3]:
                    # Finnhub returns gradeTime as unix seconds
                    gt = ch.get("gradeTime")
                    date_str = ""
                    if gt:
                        try:
                            date_str = date.fromtimestamp(int(gt)).isoformat()
                        except (ValueError, TypeError, OSError):
                            date_str = str(gt)
                    recent_changes.append({
                        "date": date_str,
                        "action": ch.get("action"),
                        "from_grade": ch.get("fromGrade"),
                        "to_grade": ch.get("toGrade"),
                        "firm": ch.get("company"),
                    })
            except finnhub.FinnhubAPIException as e:
                if getattr(e, "status_code", None) == 403:
                    if not _ENDPOINT_DISABLED["upgrade_downgrade"]:
                        logger.warning(
                            "finnhub_endpoint_disabled_403",
                            extra={"endpoint": "upgrade_downgrade",
                                   "detail": "Tier does not include this endpoint; disabling for session."},
                        )
                        _ENDPOINT_DISABLED["upgrade_downgrade"] = True
                else:
                    logger.warning("Finnhub upgrade_downgrade failed for %s", symbol, exc_info=True)
            except Exception:
                logger.warning("Finnhub upgrade_downgrade failed for %s", symbol, exc_info=True)

        result = {
            "recommendation": recommendation,
            "price_target": price_target,
            "recent_rating_changes": recent_changes,
        }
        _analyst_cache[symbol] = (time.monotonic(), result)
        return result

    # ── Company profile + key metrics ──────────────────────

    def get_company_profile(self, symbol: str) -> dict:
        """Return company profile and key metrics from Finnhub.

        Combines /stock/profile2 (sector, market_cap) and /stock/metric
        (PE ratio, dividend yield, 52-week high/low). Both endpoints are
        confirmed free-tier per Stage 4 investigation (2026-04-26).

        ETFs return empty {} from /stock/profile2 (structural — not a tier
        limit). ETFs get a sparse ~19-field /stock/metric response; 52-week
        high/low IS populated, PE and dividend yield are not.

        Returns dict with keys:
            sector: str | None              finnhubIndustry from /stock/profile2
            market_cap: float | None        marketCapitalization (millions USD)
            pe_ratio: float | None          peBasicExclExtraTTM
            annual_dividend_yield: float | None  dividendYieldIndicatedAnnual/100
            fifty_two_week_high: float | None
            fifty_two_week_low: float | None
            profile_data_available: bool    True if /stock/profile2 non-empty
            metric_data_available: bool     True if /stock/metric non-empty

        Cached for 6 hours per symbol.
        """
        cached = _profile_cache.get(symbol)
        if cached is not None and time.monotonic() - cached[0] < _PROFILE_TTL:
            return cached[1]

        profile_raw: dict = {}
        profile_ok = False
        try:
            profile_raw = self._client.company_profile2(symbol=symbol) or {}
            # Empty dict is structural for ETFs, not a failure.
            profile_ok = bool(profile_raw)
        except Exception:
            logger.warning("Finnhub company_profile2 failed for %s", symbol, exc_info=True)

        metric_raw: dict = {}
        metric_ok = False
        try:
            metric_resp = self._client.company_basic_financials(symbol, "all") or {}
            metric_raw = metric_resp.get("metric") or {}
            metric_ok = bool(metric_raw)
        except Exception:
            logger.warning(
                "Finnhub company_basic_financials failed for %s", symbol, exc_info=True,
            )

        # dividendYieldIndicatedAnnual is in percent (e.g. 1.5 means 1.5%).
        # Normalize to decimal fraction to match yfinance convention (0.015).
        div_yield_pct = metric_raw.get("dividendYieldIndicatedAnnual")
        div_yield = div_yield_pct / 100.0 if div_yield_pct is not None else None

        result = {
            "sector": profile_raw.get("finnhubIndustry") or None,
            "market_cap": profile_raw.get("marketCapitalization"),
            "pe_ratio": metric_raw.get("peBasicExclExtraTTM") or metric_raw.get("peNormalizedAnnual"),
            "annual_dividend_yield": div_yield,
            "fifty_two_week_high": metric_raw.get("52WeekHigh"),
            "fifty_two_week_low": metric_raw.get("52WeekLow"),
            "profile_data_available": profile_ok,
            "metric_data_available": metric_ok,
        }

        _profile_cache[symbol] = (time.monotonic(), result)
        logger.info(
            "Finnhub company profile for %s: sector=%r pe=%s div_yield=%s 52wk=%s/%s",
            symbol, result["sector"], result["pe_ratio"], result["annual_dividend_yield"],
            result["fifty_two_week_high"], result["fifty_two_week_low"],
        )
        return result

    # ── News sentiment ──────────────────────────────────────

    def get_news_sentiment(self, symbol: str) -> Optional[dict]:
        """Return Finnhub NLP news sentiment + buzz ratio. 1h cache."""
        cached = _sentiment_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < _SENTIMENT_TTL:
            return cached[1]

        if _ENDPOINT_DISABLED["news_sentiment"]:
            return None

        try:
            payload = self._client.news_sentiment(symbol) or {}
        except finnhub.FinnhubAPIException as e:
            if getattr(e, "status_code", None) == 403:
                if not _ENDPOINT_DISABLED["news_sentiment"]:
                    logger.warning(
                        "finnhub_endpoint_disabled_403",
                        extra={"endpoint": "news_sentiment",
                               "detail": "Tier does not include this endpoint; disabling for session."},
                    )
                    _ENDPOINT_DISABLED["news_sentiment"] = True
            else:
                logger.warning("Finnhub news_sentiment failed for %s", symbol, exc_info=True)
            return None
        except Exception:
            logger.warning("Finnhub news_sentiment failed for %s", symbol, exc_info=True)
            return None

        sentiment = payload.get("sentiment") or {}
        buzz = payload.get("buzz") or {}

        articles_week = buzz.get("articlesInLastWeek")
        weekly_avg = buzz.get("weeklyAverage")
        try:
            buzz_ratio = (
                articles_week / weekly_avg
                if articles_week is not None and weekly_avg
                else None
            )
        except (TypeError, ZeroDivisionError):
            buzz_ratio = None

        result = {
            "bullish_pct": sentiment.get("bullishPercent"),
            "bearish_pct": sentiment.get("bearishPercent"),
            "company_score": payload.get("companyNewsScore"),
            "articles_this_week": articles_week,
            "weekly_avg_articles": weekly_avg,
            "buzz_ratio": round(buzz_ratio, 3) if buzz_ratio is not None else None,
        }
        _sentiment_cache[symbol] = (time.monotonic(), result)
        return result
