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
_IVRANK_TTL = 30 * 60
_CORES_TTL = 6 * 3600
_STRIKES_TTL = 15 * 60

_summary_cache: dict[str, tuple[float, dict]] = {}
_earnings_cache: dict[str, tuple[float, dict]] = {}
_ivrank_cache: dict[str, tuple[float, dict]] = {}
_cores_cache: dict[str, tuple[float, dict]] = {}
_strikes_cache: dict[str, tuple[float, list]] = {}


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
                "ex_ern_iv_30d": self._safe_float(row.get("exErnIv30d")),
                "contango": self._safe_float(row.get("contango")),
                "skewing": self._safe_float(row.get("skewing")),
                "implied_earnings_move": self._safe_float(row.get("impliedEarningsMove")),
                "rip": self._safe_float(row.get("rip")),
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

    def get_iv_rank_batch(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch IV rank/percentile for up to 10 tickers in one call.

        Returns a dict keyed by ticker. Cached per-ticker for 30 minutes
        (cache hits skip the API entirely; misses go in one batch call).
        Never raises — returns an empty dict on full failure.
        """
        if not symbols:
            return {}

        result: dict[str, dict] = {}
        now = time.monotonic()
        misses: list[str] = []
        for sym in symbols:
            cached = _ivrank_cache.get(sym.upper())
            if cached and now - cached[0] < _IVRANK_TTL:
                result[sym.upper()] = cached[1]
            else:
                misses.append(sym.upper())

        if not misses:
            return result

        # ORATS limits batch ticker queries; chunk to 10 at a time.
        for i in range(0, len(misses), 10):
            chunk = misses[i:i + 10]
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/ivrank",
                    params={"token": self.api_key, "ticker": ",".join(chunk)},
                    timeout=self.TIMEOUT,
                )
                resp.raise_for_status()
                rows = resp.json().get("data", []) or []
            except Exception:
                logger.warning(
                    "ORATS /ivrank failed for chunk %s", chunk, exc_info=True,
                )
                continue

            for row in rows:
                ticker = str(row.get("ticker", "")).upper()
                if not ticker:
                    continue
                entry = {
                    "iv": self._safe_float(row.get("iv")),
                    "ivRank1y": self._safe_float(row.get("ivRank1y")),
                    "ivPct1y": self._safe_float(row.get("ivPct1y")),
                    "ivRank1m": self._safe_float(row.get("ivRank1m")),
                    "ivPct1m": self._safe_float(row.get("ivPct1m")),
                }
                _ivrank_cache[ticker] = (time.monotonic(), entry)
                result[ticker] = entry

        return result

    def get_cores(self, symbol: str) -> Optional[dict]:
        """Fetch the ORATS /cores endpoint and extract the fields we use.

        Cached for 6 hours. Returns None on failure.
        """
        key = symbol.upper()
        cached = _cores_cache.get(key)
        if cached and time.monotonic() - cached[0] < _CORES_TTL:
            return cached[1]

        try:
            resp = requests.get(
                f"{self.BASE_URL}/cores",
                params={"token": self.api_key, "ticker": key},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json().get("data", []) or []
            if not rows:
                logger.warning("ORATS /cores returned empty data for %s", key)
                return None

            row = rows[0]
            next_ern = row.get("nextErn")
            if next_ern in ("0000-00-00", ""):
                next_ern = None

            result = {
                "next_earnings_date": next_ern,
                "days_to_next_earnings": row.get("daysToNextErn"),
                "abs_avg_earnings_move": self._safe_float(row.get("absAvgErnMv")),
                "implied_earnings_move": self._safe_float(row.get("impliedEarningsMove")),
                "iv_hv_ratio": self._safe_float(row.get("ivHvXernRatio")),
                "iv_hv_ratio_1y_avg": self._safe_float(row.get("ivHvXernRatio1y")),
                "vol_of_vol": self._safe_float(row.get("volOfVol")),
                "skew_percentile": self._safe_float(row.get("slopepctile")),
                "skew_1y_avg": self._safe_float(row.get("slopeavg1y")),
                "hv_20d": self._safe_float(row.get("orHv20d")),
                "hv_30d": self._safe_float(row.get("orHv30d")),
                "hv_ex_earnings_20d": self._safe_float(row.get("orHvXern20d")),
                "rip": self._safe_float(row.get("rip")),
                "best_etf": row.get("bestEtf"),
                "sector_name": row.get("sectorName"),
            }
            _cores_cache[key] = (time.monotonic(), result)
            return result

        except Exception:
            logger.warning("ORATS /cores failed for %s", key, exc_info=True)
            return None

    def get_strikes_by_delta(
        self,
        symbol: str,
        option_type: str,
        delta_min: float,
        delta_max: float,
        dte_min: int,
        dte_max: int,
    ) -> list[dict]:
        """Fetch strikes filtered by delta and DTE ranges.

        ORATS' /strikes returns both put and call data per strike row;
        we project the side requested. Put deltas are returned by
        ORATS as negative values, so the delta filter for puts is
        applied with negative bounds (delta_min/delta_max are passed
        as positive magnitudes by callers).

        Cached for 15 minutes per (symbol, side, delta range, DTE range).
        Returns an empty list on failure.
        """
        side = option_type.lower()
        if side not in ("put", "call"):
            raise ValueError(f"option_type must be 'put' or 'call', got {option_type!r}")

        cache_key = f"{symbol.upper()}|{side}|{delta_min}|{delta_max}|{dte_min}|{dte_max}"
        cached = _strikes_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < _STRIKES_TTL:
            return cached[1]

        # Puts come back with negative deltas — invert and swap the bounds.
        if side == "put":
            d_lo, d_hi = -abs(delta_max), -abs(delta_min)
        else:
            d_lo, d_hi = abs(delta_min), abs(delta_max)

        try:
            resp = requests.get(
                f"{self.BASE_URL}/strikes",
                params={
                    "token": self.api_key,
                    "ticker": symbol.upper(),
                    "delta": f"{d_lo},{d_hi}",
                    "dte": f"{dte_min},{dte_max}",
                },
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS /strikes failed for %s %s d=%s,%s dte=%s,%s",
                symbol, side, d_lo, d_hi, dte_min, dte_max, exc_info=True,
            )
            return []

        contracts: list[dict] = []
        for row in rows:
            if side == "put":
                bid = self._safe_float(row.get("putBidPrice"))
                ask = self._safe_float(row.get("putAskPrice"))
                oi = row.get("putOpenInterest")
                opt_value = self._safe_float(row.get("putValue"))
                delta = self._safe_float(row.get("delta"))
            else:
                bid = self._safe_float(row.get("callBidPrice"))
                ask = self._safe_float(row.get("callAskPrice"))
                oi = row.get("callOpenInterest")
                opt_value = self._safe_float(row.get("callValue"))
                delta = self._safe_float(row.get("delta"))

            mid = None
            if bid is not None and ask is not None:
                mid = round((bid + ask) / 2, 4)

            contracts.append({
                "strike": self._safe_float(row.get("strike")),
                "expiration_date": str(row.get("expirDate", "")),
                "dte": row.get("dte"),
                "delta": delta,
                "theta": self._safe_float(row.get("theta")),
                "vega": self._safe_float(row.get("vega")),
                "gamma": self._safe_float(row.get("gamma")),
                "smv_vol": self._safe_float(row.get("smvVol")),
                "bid_price": bid,
                "ask_price": ask,
                "mid_price": mid,
                "open_interest": oi,
                "opt_value": opt_value,
            })

        _strikes_cache[cache_key] = (time.monotonic(), contracts)
        return contracts

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
