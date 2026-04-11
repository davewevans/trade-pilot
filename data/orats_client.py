"""ORATS API client for professional options analytics.

Provides IV rank, IV percentile, ATM IV, term structure,
skew, and expected move data.
"""

import logging
import math
import time
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

_SUMMARY_TTL = 30 * 60
_EARNINGS_TTL = 6 * 3600

_summary_cache: dict[str, tuple[float, dict]] = {}
_earnings_cache: dict[str, tuple[float, dict]] = {}


class ORATSClient:
    """Client for the ORATS Datav2 API."""

    BASE_URL = "https://api.orats.io/datav2"
    TIMEOUT = 10

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.ORATS_API_KEY

    def get_summary(self, symbol: str) -> Optional[dict]:
        """Fetch the ORATS summary for a symbol.

        Returns a normalized dict with IV rank, ATM IV, skew, expected
        move, and term structure fields.  Cached for 30 minutes.
        Returns None on failure.
        """
        cached = _summary_cache.get(symbol)
        if cached:
            ts, data = cached
            if time.monotonic() - ts < _SUMMARY_TTL:
                return data

        try:
            resp = requests.get(
                f"{self.BASE_URL}/summaries",
                params={"token": self.api_key, "ticker": symbol},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("data", [])
            if not rows:
                logger.warning("ORATS /summaries returned empty data for %s", symbol)
                return None

            row = rows[0]

            atm_m1 = self._safe_float(row.get("atmIvM1"))
            atm_m2 = self._safe_float(row.get("atmIvM2"))
            term_slope = None
            if atm_m1 is not None and atm_m2 is not None:
                term_slope = round(atm_m2 - atm_m1, 4)

            result = {
                "ticker": symbol.upper(),
                "iv_rank_1y": self._safe_float(row.get("ivRank1y")),
                "iv_rank_1m": self._safe_float(row.get("ivRank1m")),
                "iv_pct_1y": self._safe_float(row.get("ivPct1y")),
                "iv_pct_1m": self._safe_float(row.get("ivPct1m")),
                "atm_iv_m1": atm_m1,
                "atm_iv_m2": atm_m2,
                "atm_iv_m3": self._safe_float(row.get("atmIvM3")),
                "atm_iv_m4": self._safe_float(row.get("atmIvM4")),
                "skew_m1": self._safe_float(row.get("iSkewM1")),
                "skew_m2": self._safe_float(row.get("iSkewM2")),
                "implied_move_pct": self._safe_float(row.get("impliedMove")),
                "forecast_move_pct": self._safe_float(row.get("fcstMove")),
                "stock_price": self._safe_float(
                    row.get("stockPrice") or row.get("stkPx")
                ),
                "trade_date": str(row.get("tradeDate", "")),
                "term_structure_slope": term_slope,
            }

            _summary_cache[symbol] = (time.monotonic(), result)
            logger.info(
                "ORATS summary for %s: iv_rank_1y=%.1f atm_iv_m1=%.3f "
                "skew_m1=%.3f implied_move=%.1f%%",
                symbol,
                result["iv_rank_1y"] or 0,
                result["atm_iv_m1"] or 0,
                result["skew_m1"] or 0,
                result["implied_move_pct"] or 0,
            )
            return result

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                logger.error("ORATS API key is invalid or expired")
            else:
                logger.warning("ORATS /summaries HTTP error for %s: %s", symbol, e)
        except Exception:
            logger.warning("ORATS /summaries failed for %s", symbol, exc_info=True)
        return None

    def get_earnings(self, symbol: str) -> Optional[dict]:
        """Fetch the next upcoming earnings date for a symbol.

        Returns ``{next_earnings_date, after_close, days_to_earnings}``
        or None on failure.  Cached for 6 hours.
        """
        cached = _earnings_cache.get(symbol)
        if cached:
            ts, data = cached
            if time.monotonic() - ts < _EARNINGS_TTL:
                return data

        try:
            resp = requests.get(
                f"{self.BASE_URL}/earnings",
                params={"token": self.api_key, "ticker": symbol},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("data", [])

            from datetime import date

            today = date.today()
            future_rows = []
            for row in rows:
                try:
                    ed = date.fromisoformat(str(row.get("earnDate", "")))
                    if ed >= today:
                        future_rows.append((ed, row))
                except (ValueError, TypeError):
                    continue

            if not future_rows:
                result = {
                    "next_earnings_date": None,
                    "after_close": None,
                    "days_to_earnings": None,
                }
            else:
                future_rows.sort(key=lambda x: x[0])
                ed, row = future_rows[0]
                result = {
                    "next_earnings_date": str(ed),
                    "after_close": bool(row.get("anncAfterClose", True)),
                    "days_to_earnings": (ed - today).days,
                }

            _earnings_cache[symbol] = (time.monotonic(), result)
            logger.info(
                "ORATS earnings for %s: date=%s days=%s",
                symbol, result["next_earnings_date"], result["days_to_earnings"],
            )
            return result

        except Exception:
            logger.warning("ORATS /earnings failed for %s", symbol, exc_info=True)
            return None

    @staticmethod
    def classify_iv_environment(iv_rank_1y: Optional[float]) -> str:
        """Classify IV rank into LOW / MODERATE / HIGH / UNKNOWN."""
        if iv_rank_1y is None:
            return "UNKNOWN"
        if iv_rank_1y < 30:
            return "LOW"
        if iv_rank_1y < 50:
            return "MODERATE"
        return "HIGH"

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) or math.isinf(f) else round(f, 4)
        except (TypeError, ValueError):
            return None
