"""ORATS API client for professional options analytics.

Provides IV rank, IV percentile, ATM IV, term structure,
skew, and expected move data.
"""

import logging
import math
import os
import time
from typing import Optional

import requests

from config import settings
from data.orats_cache import ORATSCache

logger = logging.getLogger(__name__)

_SUMMARY_TTL = 30 * 60
_EARNINGS_TTL = 6 * 3600
_IVRANK_TTL = 30 * 60
_CORES_TTL = 6 * 3600
_STRIKES_TTL = 15 * 60
_MONIES_TTL = 30 * 60

_cache = ORATSCache()

# Apply debug-level logging when operator sets ORATS_DEBUG_LOGGING=true.
if settings.ORATS_DEBUG_LOGGING:
    logger.setLevel(logging.DEBUG)

# ── Canonical /strikes fetch parameters ───────────────────────────────────────
# The cache key for strikes is (symbol, side) only — the same canonical range
# is always fetched from ORATS, and callers filter the result in Python via
# filter_strikes().  The canonical bounds must remain a strict superset of
# every call site's actual delta/DTE requirements.
#
# Current call-site maxima (verify with grep get_strikes_by_delta):
#   delta: 0.15–0.65   →  canonical: 0.10–0.70
#   DTE:   20–45       →  canonical: 14–60
_CANONICAL_STRIKES_DELTA_MIN: float = 0.10
_CANONICAL_STRIKES_DELTA_MAX: float = 0.70
_CANONICAL_STRIKES_DTE_MIN: int = 14
_CANONICAL_STRIKES_DTE_MAX: int = 60


def filter_strikes(
    strikes: list[dict],
    delta_min: float,
    delta_max: float,
    dte_min: int,
    dte_max: int,
) -> list[dict]:
    """Filter a canonical-range strike list to a caller's narrow range.

    Compares delta by absolute magnitude so put deltas (which ORATS returns as
    negative values) work correctly alongside positive caller inputs.

    Args:
        strikes:   Full strike list as returned by get_strikes_by_delta().
        delta_min: Minimum absolute delta (inclusive).
        delta_max: Maximum absolute delta (inclusive).
        dte_min:   Minimum DTE (inclusive).
        dte_max:   Maximum DTE (inclusive).

    Returns:
        Filtered list preserving original ordering.
    """
    result = []
    for s in strikes:
        delta = s.get("delta")
        dte = s.get("dte")
        if delta is not None and not (delta_min <= abs(delta) <= delta_max):
            continue
        if dte is not None and not (dte_min <= dte <= dte_max):
            continue
        result.append(s)
    return result

# Module-level consecutive failure counters per endpoint.
# Reset to 0 on success; fire a warning notification when threshold is hit.
_consecutive_failures: dict[str, int] = {}
_FAILURE_ALERT_THRESHOLD = 3


def _record_failure(endpoint: str) -> None:
    """Increment consecutive failure counter and fire alert at threshold."""
    _consecutive_failures[endpoint] = _consecutive_failures.get(endpoint, 0) + 1
    if _consecutive_failures[endpoint] == _FAILURE_ALERT_THRESHOLD:
        try:
            from notifications import notify
            notify(
                "warning",
                f"ORATS {endpoint} failing",
                f"{_FAILURE_ALERT_THRESHOLD} consecutive failures on {endpoint}",
                tags=["data_source", "orats"],
            )
        except Exception:
            pass


def _record_success(endpoint: str) -> None:
    """Reset consecutive failure counter on success."""
    _consecutive_failures[endpoint] = 0


class ORATSClient:
    """Client for the ORATS Datav2 API."""

    BASE_URL = "https://api.orats.io/datav2"
    TIMEOUT = 10

    def __init__(self, api_key: str | None = None):
        from datetime import datetime as _datetime
        self.api_key = api_key or settings.ORATS_API_KEY
        self._call_count: int = 0
        self._calls_by_endpoint: dict[str, int] = {}
        self._session_start: _datetime = _datetime.utcnow()

    def _track_call(self, endpoint: str) -> None:
        """Increment the per-endpoint and total call counters."""
        self._call_count += 1
        self._calls_by_endpoint[endpoint] = self._calls_by_endpoint.get(endpoint, 0) + 1

    def get_usage(self) -> dict:
        """Return current session call counts and timing."""
        from datetime import datetime as _datetime
        return {
            "total_calls": self._call_count,
            "by_endpoint": dict(self._calls_by_endpoint),
            "session_start": self._session_start.isoformat(),
            "window_seconds": (_datetime.utcnow() - self._session_start).total_seconds(),
        }

    def reset_usage(self) -> None:
        """Reset counters and start a new session window."""
        from datetime import datetime as _datetime
        self._call_count = 0
        self._calls_by_endpoint = {}
        self._session_start = _datetime.utcnow()

    def get_summary(self, symbol: str) -> Optional[dict]:
        """Fetch the ORATS summary for a symbol.

        Returns a normalized dict with IV rank, ATM IV, skew, expected
        move, and term structure fields.  Cached for 30 minutes.
        Returns None on failure.
        """
        cached = _cache.get("summaries", symbol.upper(), _SUMMARY_TTL)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_live", "summaries", symbol, True, None, None, _job_name)
            return cached

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_live", "summaries", symbol, _job_name)
        self._track_call("summaries")
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        _result: Optional[dict] = None
        try:
            resp = requests.get(
                f"{self.BASE_URL}/summaries",
                params={"token": self.api_key, "ticker": symbol},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            _status_code = resp.status_code
            payload = resp.json()
            rows = payload.get("data", [])
            if not rows:
                logger.warning("ORATS /summaries returned empty data for %s", symbol)
            else:
                row = rows[0]

                atm_m1 = self._safe_float(row.get("atmIvM1"))
                atm_m2 = self._safe_float(row.get("atmIvM2"))
                term_slope = None
                if atm_m1 is not None and atm_m2 is not None:
                    term_slope = round(atm_m2 - atm_m1, 4)

                _result = {
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

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                logger.error("ORATS API key is invalid or expired")
                _status_code = e.response.status_code
            else:
                logger.warning("ORATS /summaries HTTP error for %s: %s", symbol, e)
                if e.response is not None:
                    _status_code = e.response.status_code
        except Exception:
            logger.warning("ORATS /summaries failed for %s", symbol, exc_info=True)
        finally:
            get_ledger().record("orats_live", "summaries", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if _result is None:
            _record_failure("summaries")
            return None

        _cache.set("summaries", symbol.upper(), _result, _SUMMARY_TTL)
        logger.info(
            "ORATS summary for %s: iv_rank_1y=%.1f atm_iv_m1=%.3f "
            "skew_m1=%.3f implied_move=%.1f%%",
            symbol,
            _result["iv_rank_1y"] or 0,
            _result["atm_iv_m1"] or 0,
            _result["skew_m1"] or 0,
            _result["implied_move_pct"] or 0,
        )
        _record_success("summaries")
        return _result

    def get_earnings(self, symbol: str) -> Optional[dict]:
        """Fetch the next upcoming earnings date for a symbol.

        Returns ``{next_earnings_date, after_close, days_to_earnings}``
        or None on failure.  Cached for 6 hours.
        """
        cached = _cache.get("earnings", symbol.upper(), _EARNINGS_TTL)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_live", "earnings", symbol, True, None, None, _job_name)
            return cached

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_live", "earnings", symbol, _job_name)
        self._track_call("earnings")
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        _result: Optional[dict] = None
        try:
            resp = requests.get(
                f"{self.BASE_URL}/earnings",
                params={"token": self.api_key, "ticker": symbol},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            _status_code = resp.status_code
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
                _result = {
                    "next_earnings_date": None,
                    "after_close": None,
                    "days_to_earnings": None,
                }
            else:
                future_rows.sort(key=lambda x: x[0])
                ed, row = future_rows[0]
                _result = {
                    "next_earnings_date": str(ed),
                    "after_close": bool(row.get("anncAfterClose", True)),
                    "days_to_earnings": (ed - today).days,
                }

        except Exception:
            logger.warning("ORATS /earnings failed for %s", symbol, exc_info=True)
        finally:
            get_ledger().record("orats_live", "earnings", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if _result is None:
            _record_failure("earnings")
            return None

        _cache.set("earnings", symbol.upper(), _result, _EARNINGS_TTL)
        logger.info(
            "ORATS earnings for %s: date=%s days=%s",
            symbol, _result["next_earnings_date"], _result["days_to_earnings"],
        )
        _record_success("earnings")
        return _result

    def get_iv_rank_batch(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch IV rank/percentile for up to 10 tickers in one call.

        Returns a dict keyed by ticker. Cached per-ticker for 30 minutes
        (cache hits skip the API entirely; misses go in one batch call).
        Never raises — returns an empty dict on full failure.
        """
        if not symbols:
            return {}

        from data.api_ledger import get_ledger
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")

        result: dict[str, dict] = {}
        misses: list[str] = []
        for sym in symbols:
            cached = _cache.get("ivrank", sym.upper(), _IVRANK_TTL)
            if cached is not None:
                result[sym.upper()] = cached
                get_ledger().record("orats_live", "ivrank", sym, True, None, None, _job_name)
            else:
                misses.append(sym.upper())

        if not misses:
            return result

        # ORATS limits batch ticker queries; chunk to 10 at a time.
        for i in range(0, len(misses), 10):
            chunk = misses[i:i + 10]
            get_ledger().check_and_reserve("orats_live", "ivrank", None, _job_name)
            self._track_call("ivrank")
            _t0 = time.monotonic()
            _status_code: Optional[int] = None
            _chunk_rows: list = []
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/ivrank",
                    params={"token": self.api_key, "ticker": ",".join(chunk)},
                    timeout=self.TIMEOUT,
                )
                resp.raise_for_status()
                _status_code = resp.status_code
                _chunk_rows = resp.json().get("data", []) or []
            except Exception:
                logger.warning(
                    "ORATS /ivrank failed for chunk %s", chunk, exc_info=True,
                )
            finally:
                get_ledger().record("orats_live", "ivrank", None, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

            if _status_code is None:
                _record_failure("ivrank")
                continue

            _record_success("ivrank")
            for row in _chunk_rows:
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
                _cache.set("ivrank", ticker, entry, _IVRANK_TTL)
                result[ticker] = entry

        return result

    def get_cores(self, symbol: str) -> Optional[dict]:
        """Fetch the ORATS /cores endpoint and extract the fields we use.

        Cached for 6 hours. Returns None on failure.
        """
        key = symbol.upper()
        cached = _cache.get("cores", key, _CORES_TTL)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_live", "cores", symbol, True, None, None, _job_name)
            return cached

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_live", "cores", symbol, _job_name)
        self._track_call("cores")
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        _result: Optional[dict] = None
        try:
            resp = requests.get(
                f"{self.BASE_URL}/cores",
                params={"token": self.api_key, "ticker": key},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            _status_code = resp.status_code
            raw_response = resp.json()
            logger.debug(
                "orats_cores_raw_response",
                extra={
                    "symbol": symbol,
                    "response_type": type(raw_response).__name__,
                    "response_len": len(raw_response) if hasattr(raw_response, "__len__") else None,
                    "response_preview": repr(raw_response)[:500],
                },
            )
            if not isinstance(raw_response, dict):
                logger.warning(
                    "orats_cores_unexpected_response_type",
                    extra={"symbol": symbol, "response_type": type(raw_response).__name__,
                           "response_preview": repr(raw_response)[:200]},
                )
            else:
                rows = raw_response.get("data", []) or []
                if not rows:
                    logger.warning("ORATS /cores returned empty data for %s", key)
                elif not isinstance(rows, list):
                    logger.warning(
                        "orats_cores_data_not_list",
                        extra={"symbol": symbol, "data_type": type(rows).__name__,
                               "data_preview": repr(rows)[:200]},
                    )
                else:
                    row = rows[0]
                    next_ern = row.get("nextErn")
                    if next_ern in ("0000-00-00", ""):
                        next_ern = None

                    _result = {
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
                        "or_fcst_20d": self._safe_float(row.get("orFcst20d")),
                        "or_iv_fcst_20d": self._safe_float(row.get("orIvFcst20d")),
                        "or_fcst_inf": self._safe_float(row.get("orFcstInf")),
                        "ex_ern_iv_20d": self._safe_float(row.get("exErnIv20d")),
                        "ex_ern_iv_30d": self._safe_float(row.get("exErnIv30d")),
                        "atm_iv_m1": self._safe_float(row.get("atmIvM1")),
                        "atm_iv_m2": self._safe_float(row.get("atmIvM2")),
                        "atm_iv_m3": self._safe_float(row.get("atmIvM3")),
                        "atm_iv_m4": self._safe_float(row.get("atmIvM4")),
                        "slope": self._safe_float(row.get("slope")),
                        "slope_fcst": self._safe_float(row.get("slopeFcst")),
                        "slope_inf": self._safe_float(row.get("slopeInf")),
                        "contango": self._safe_float(row.get("contango")),
                        "contango_fcst": self._safe_float(row.get("contangoFcst")),
                        "deriv": self._safe_float(row.get("deriv")),
                        "fwd_ratio_20_30": self._safe_float(row.get("fwdRatio2030")),
                        "fwd_ratio_30_60": self._safe_float(row.get("fwdRatio3060")),
                        "fwd_ratio_60_90": self._safe_float(row.get("fwdRatio6090")),
                        "confidence": self._safe_float(row.get("confidence")),
                        "r_squared": self._safe_float(row.get("rSquared")),
                    }

        except Exception:
            logger.warning("ORATS /cores failed for %s", key, exc_info=True)
        finally:
            get_ledger().record("orats_live", "cores", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if _result is None:
            _record_failure("cores")
            return None

        _cache.set("cores", key, _result, _CORES_TTL)
        _record_success("cores")
        return _result

    def get_monies(self, symbol: str) -> list[dict]:
        """Fetch implied volatility at standardized delta levels (the vol smile).

        Calls ``/monies/implied`` which returns one row per expiration, each
        containing the smoothed IV at delta-level buckets:

            vol5  … vol100   where vol100 ≈ ATM, vol95 = 95-delta put,
                              vol30 = 30-delta put, vol5 = 5-delta put.

        These are ORATS' model-smoothed (SMV) vols so they are free of
        bid/ask noise and reflect the true vol surface shape.

        Cached for 30 minutes per symbol. Returns an empty list on failure.
        """
        key = symbol.upper()
        cached = _cache.get("monies", key, _MONIES_TTL)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_live", "monies", symbol, True, None, None, _job_name)
            return cached

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_live", "monies", symbol, _job_name)
        self._track_call("monies")
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        rows: list = []
        try:
            resp = requests.get(
                f"{self.BASE_URL}/monies/implied",
                params={"token": self.api_key, "ticker": key},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            _status_code = resp.status_code
            rows = resp.json().get("data", []) or []
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                logger.error("ORATS API key is invalid or expired (monies)")
                _status_code = e.response.status_code
            else:
                logger.warning("ORATS /monies/implied HTTP error for %s: %s", key, e)
                if e.response is not None:
                    _status_code = e.response.status_code
        except Exception:
            logger.warning("ORATS /monies/implied failed for %s", key, exc_info=True)
        finally:
            get_ledger().record("orats_live", "monies", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if _status_code is None:
            _record_failure("monies")
            return []

        # Normalize: pull all vol{N} fields plus metadata
        normalized: list[dict] = []
        for row in rows:
            entry: dict = {
                "ticker": key,
                "expir_date": str(row.get("expirDate", "")),
            }
            # Extract volXX fields (vol5, vol10, …, vol95, vol100)
            for delta_level in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50,
                                 55, 60, 65, 70, 75, 80, 85, 90, 95, 100):
                field = f"vol{delta_level}"
                entry[field] = self._safe_float(row.get(field))
            normalized.append(entry)

        _cache.set("monies", key, normalized, _MONIES_TTL)
        logger.info(
            "ORATS monies for %s: %d expiration rows fetched", key, len(normalized),
        )
        _record_success("monies")
        return normalized

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

        # One canonical cache key per (symbol, side) — delta/DTE range is NOT part of
        # the key.  We always fetch the canonical-wide range from ORATS and filter in
        # Python, so any two callers with overlapping ranges share the same cache entry.
        cache_key = f"{symbol.upper()}|{side}"
        cached = _cache.get("strikes", cache_key, _STRIKES_TTL)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_live", "strikes", symbol, True, None, None, _job_name)
            return filter_strikes(cached, delta_min, delta_max, dte_min, dte_max)

        # Fetch the full canonical range from ORATS.
        # Puts come back with negative deltas — invert and swap the bounds.
        if side == "put":
            d_lo = -abs(_CANONICAL_STRIKES_DELTA_MAX)
            d_hi = -abs(_CANONICAL_STRIKES_DELTA_MIN)
        else:
            d_lo = abs(_CANONICAL_STRIKES_DELTA_MIN)
            d_hi = abs(_CANONICAL_STRIKES_DELTA_MAX)

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_live", "strikes", symbol, _job_name)
        self._track_call("strikes")
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        rows: list = []
        try:
            resp = requests.get(
                f"{self.BASE_URL}/strikes",
                params={
                    "token": self.api_key,
                    "ticker": symbol.upper(),
                    "delta": f"{d_lo},{d_hi}",
                    "dte": f"{_CANONICAL_STRIKES_DTE_MIN},{_CANONICAL_STRIKES_DTE_MAX}",
                },
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            _status_code = resp.status_code
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS /strikes failed for %s %s canonical d=%s,%s dte=%s,%s",
                symbol, side, d_lo, d_hi,
                _CANONICAL_STRIKES_DTE_MIN, _CANONICAL_STRIKES_DTE_MAX, exc_info=True,
            )
        finally:
            get_ledger().record("orats_live", "strikes", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if _status_code is None:
            _record_failure("strikes")
            return []

        _record_success("strikes")
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

        # Cache the full canonical set; callers filter via filter_strikes().
        _cache.set("strikes", cache_key, contracts, _STRIKES_TTL)
        return filter_strikes(contracts, delta_min, delta_max, dte_min, dte_max)

    def get_snapshots_by_strike(
        self,
        symbol: str,
        option_type: str,
        dte_min: int,
        dte_max: int,
        delta_min: float,
        delta_max: float,
    ) -> dict[tuple[str, float], dict]:
        """Return ORATS greeks and quotes indexed by (expiration_date, strike).

        Internally calls :meth:`get_strikes_by_delta` and reuses its cache,
        so no new HTTP request is made if the result is already cached.

        Returns a dict keyed by ``(expiration_date, strike)`` tuples where
        ``expiration_date`` is an ISO date string (``"YYYY-MM-DD"``) and
        ``strike`` is a ``float``.

        Each value matches the snapshot shape returned by
        ``AlpacaBroker.get_option_snapshots()``:

        .. code-block:: python

            {
                "bid": float | None,
                "ask": float | None,
                "mid": float | None,
                "delta": float | None,
                "theta": float | None,
                "vega": float | None,
                "gamma": float | None,
                "iv": float | None,      # mapped from smvVol
                "open_interest": int | None,
                "last_trade_size": None,  # ORATS /strikes does not return trade size
            }

        Never raises — returns an empty dict on failure.
        """
        try:
            rows = self.get_strikes_by_delta(
                symbol=symbol,
                option_type=option_type,
                delta_min=delta_min,
                delta_max=delta_max,
                dte_min=dte_min,
                dte_max=dte_max,
            )
        except Exception:
            logger.warning(
                "get_snapshots_by_strike: get_strikes_by_delta failed for %s %s",
                symbol, option_type, exc_info=True,
            )
            return {}

        result: dict[tuple[str, float], dict] = {}
        for row in rows:
            exp_date = row.get("expiration_date", "")
            strike = row.get("strike")
            if not exp_date or strike is None:
                continue
            result[(exp_date, float(strike))] = {
                "bid": row.get("bid_price"),
                "ask": row.get("ask_price"),
                "mid": row.get("mid_price"),
                "delta": row.get("delta"),
                "theta": row.get("theta"),
                "vega": row.get("vega"),
                "gamma": row.get("gamma"),
                "iv": row.get("smv_vol"),
                "open_interest": row.get("open_interest"),
                "last_trade_size": None,
            }

        logger.info(
            "ORATS snapshots_by_strike: %s %s dte=%d-%d delta=%.2f-%.2f → %d strikes",
            symbol, option_type, dte_min, dte_max, delta_min, delta_max, len(result),
        )
        return result

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
