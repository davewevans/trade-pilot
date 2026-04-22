"""ORATS historical data client with persistent SQLite caching.

Wraps the /hist/* endpoints of the ORATS Datav2 API.  Historical data
is immutable, so responses are cached indefinitely in a local SQLite DB.
A simple rate-limiter keeps requests under 1000/minute (ORATS limit).
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

BASE_URL = "https://api.orats.io/datav2"
_TIMEOUT = 15


# ── SQLite cache ─────────────────────────────────────────────────────────────

# Historical data for past settled trading days is immutable — once fetched it
# never needs to be re-fetched.  Use an astronomically large TTL so SQLite
# cache rows are never treated as expired for past dates.
#
# Today's data may not have settled yet (ORATS updates intraday) so it uses a
# shorter 1-hour TTL.
_IMMUTABLE_TTL: float = 10.0 * 365.25 * 24 * 3600  # ~10 years (effectively forever)
_TODAY_TTL: float = 3600.0  # 1 hour for the current trading day

_hist_cache = ORATSCache()


def _ttl_for_date(trade_date: str) -> float:
    """Return the appropriate cache TTL for a historical data date.

    Past dates → _IMMUTABLE_TTL (data is immutable, never re-fetch).
    Today → _TODAY_TTL (data may not have settled yet).
    """
    from datetime import date as _date
    try:
        today = _date.today().isoformat()
    except Exception:
        return _TODAY_TTL
    return _TODAY_TTL if trade_date >= today else _IMMUTABLE_TTL


def _cache_get(endpoint: str, params_key: str, trade_date: str) -> Optional[list | dict]:
    return _hist_cache.get(endpoint, params_key, _ttl_for_date(trade_date))


def _cache_set(endpoint: str, params_key: str, data: list | dict, trade_date: str) -> None:
    _hist_cache.set(endpoint, params_key, data, _ttl_for_date(trade_date))


# ── Client ───────────────────────────────────────────────────────────────────

class ORATSHistorical:
    """Client for ORATS /hist/* endpoints with permanent SQLite caching."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.ORATS_API_KEY

    # ── public methods ───────────────────────────────────────────────────

    def get_strikes_on_date(
        self,
        symbol: str,
        trade_date: str,
        dte_min: int,
        dte_max: int,
        delta_min: float,
        delta_max: float,
        option_type: str = "put",
    ) -> list[dict]:
        """Fetch historical option chain for a specific date.

        Args:
            symbol: Ticker symbol (e.g. "AAPL").
            trade_date: ISO date string "YYYY-MM-DD".
            dte_min / dte_max: DTE filter range.
            delta_min / delta_max: Delta magnitudes (positive values;
                method handles sign for puts).
            option_type: "put" or "call".

        Returns:
            List of contract dicts with strike, expiration_date, dte,
            delta, bid/ask/mid prices, etc.
        """
        side = option_type.lower()
        if side == "put":
            d_lo, d_hi = -abs(delta_max), -abs(delta_min)
        else:
            d_lo, d_hi = abs(delta_min), abs(delta_max)

        params_key = f"{symbol.upper()}|{trade_date}|{dte_min}|{dte_max}|{d_lo}|{d_hi}"
        cached = _cache_get("hist/strikes", params_key, trade_date)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_historical", "hist/strikes", symbol, True, None, None, _job_name)
            return self._project_strikes(cached, side)

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_historical", "hist/strikes", symbol, _job_name)
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        rows: list = []
        try:
            resp = requests.get(
                f"{BASE_URL}/hist/strikes",
                params={
                    "token": self.api_key,
                    "ticker": symbol.upper(),
                    "tradeDate": trade_date,
                    "dte": f"{dte_min},{dte_max}",
                    "delta": f"{d_lo},{d_hi}",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            _status_code = resp.status_code
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS hist/strikes failed for %s on %s", symbol, trade_date,
                exc_info=True,
            )
        finally:
            get_ledger().record("orats_historical", "hist/strikes", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if not rows and _status_code is None:
            return []

        _cache_set("hist/strikes", params_key, rows, trade_date)
        return self._project_strikes(rows, side)

    def get_summary_on_date(self, symbol: str, trade_date: str) -> Optional[dict]:
        """Fetch historical IV summary for a specific date.

        Returns a dict with iv_rank_1y, iv_rank_1m, atm_iv_m1, skew_m1, etc.
        or None on failure.
        """
        params_key = f"{symbol.upper()}|{trade_date}"
        cached = _cache_get("hist/summaries", params_key, trade_date)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_historical", "hist/summaries", symbol, True, None, None, _job_name)
            return cached[0] if cached else None

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_historical", "hist/summaries", symbol, _job_name)
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        rows: list = []
        try:
            resp = requests.get(
                f"{BASE_URL}/hist/summaries",
                params={
                    "token": self.api_key,
                    "ticker": symbol.upper(),
                    "tradeDate": trade_date,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            _status_code = resp.status_code
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS hist/summaries failed for %s on %s", symbol, trade_date,
                exc_info=True,
            )
        finally:
            get_ledger().record("orats_historical", "hist/summaries", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if _status_code is None:
            return None

        _cache_set("hist/summaries", params_key, rows, trade_date)
        if not rows:
            return None
        return self._normalize_summary(rows[0])

    def get_cores_on_date(self, symbol: str, trade_date: str) -> Optional[dict]:
        """Fetch historical /cores data for a specific date."""
        params_key = f"{symbol.upper()}|{trade_date}"
        cached = _cache_get("hist/cores", params_key, trade_date)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_historical", "hist/cores", symbol, True, None, None, _job_name)
            return cached[0] if cached else None

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_historical", "hist/cores", symbol, _job_name)
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        rows: list = []
        try:
            resp = requests.get(
                f"{BASE_URL}/hist/cores",
                params={
                    "token": self.api_key,
                    "ticker": symbol.upper(),
                    "tradeDate": trade_date,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            _status_code = resp.status_code
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS hist/cores failed for %s on %s", symbol, trade_date,
                exc_info=True,
            )
        finally:
            get_ledger().record("orats_historical", "hist/cores", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if _status_code is None:
            return None

        _cache_set("hist/cores", params_key, rows, trade_date)
        if not rows:
            return None
        return self._normalize_cores(rows[0])

    def get_iv_rank_history(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """Fetch full IV rank history for a symbol over a date range.

        Returns a list of dicts with tradeDate, iv, ivRank1y, etc.

        end_date is capped to yesterday: ORATS hist/* endpoints only carry
        settled trading days, so requesting today's date returns 404.
        """
        from datetime import date as _date, timedelta as _td
        yesterday = (_date.today() - _td(days=1)).isoformat()
        if end_date is None or end_date > yesterday:
            end_date = yesterday

        params_key = f"{symbol.upper()}|{start_date or ''}|{end_date}"
        cached = _cache_get("hist/ivrank", params_key, end_date)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is not None:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_historical", "hist/ivrank", symbol, True, None, None, _job_name)
            return cached

        params: dict = {"token": self.api_key, "ticker": symbol.upper()}
        if start_date:
            params["tradeDate"] = f"{start_date},{end_date}"

        from data.api_ledger import get_ledger
        get_ledger().check_and_reserve("orats_historical", "hist/ivrank", symbol, _job_name)
        _t0 = time.monotonic()
        _status_code: Optional[int] = None
        rows: list = []
        try:
            resp = requests.get(
                f"{BASE_URL}/hist/ivrank",
                params=params,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 404:
                logger.warning(
                    "ORATS hist/ivrank returned 404 for %s (range %s–%s) — no data for this period",
                    symbol, start_date, end_date,
                )
                _status_code = 404
            else:
                resp.raise_for_status()
                _status_code = resp.status_code
                rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS hist/ivrank failed for %s", symbol, exc_info=True,
            )
        finally:
            get_ledger().record("orats_historical", "hist/ivrank", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)

        if _status_code is None:
            return []

        _cache_set("hist/ivrank", params_key, rows, end_date)
        return rows

    def find_contract_on_date(
        self,
        symbol: str,
        trade_date: str,
        target_strike: float,
        expiration_date: str,
        option_type: str = "put",
    ) -> Optional[dict]:
        """Look up a specific contract (by strike + expiration) on a date.

        Fetches a wide delta range to locate the specific contract.
        Returns None if not found.
        """
        from datetime import date as ddate
        try:
            exp = ddate.fromisoformat(expiration_date)
            trd = ddate.fromisoformat(trade_date)
            dte_remaining = (exp - trd).days
        except (ValueError, TypeError):
            return None

        if dte_remaining < 0:
            return None

        # Wide delta range to catch contracts that may have moved
        if option_type.lower() == "put":
            d_lo, d_hi = -0.97, -0.01
        else:
            d_lo, d_hi = 0.01, 0.97

        dte_lo = max(0, dte_remaining - 3)
        dte_hi = dte_remaining + 5

        params_key = f"{symbol.upper()}|{trade_date}|{dte_lo}|{dte_hi}|{d_lo}|{d_hi}|wide"
        cached = _cache_get("hist/strikes", params_key, trade_date)
        _job_name = os.environ.get("TRADE_PILOT_JOB_NAME")
        if cached is None:
            from data.api_ledger import get_ledger
            get_ledger().check_and_reserve("orats_historical", "hist/strikes", symbol, _job_name)
            _t0 = time.monotonic()
            _status_code: Optional[int] = None
            try:
                resp = requests.get(
                    f"{BASE_URL}/hist/strikes",
                    params={
                        "token": self.api_key,
                        "ticker": symbol.upper(),
                        "tradeDate": trade_date,
                        "dte": f"{dte_lo},{dte_hi}",
                        "delta": f"{d_lo},{d_hi}",
                    },
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                _status_code = resp.status_code
                cached = resp.json().get("data", []) or []
            except Exception:
                logger.warning(
                    "ORATS hist/strikes (wide) failed for %s on %s",
                    symbol, trade_date, exc_info=True,
                )
            finally:
                get_ledger().record("orats_historical", "hist/strikes", symbol, False, _status_code, int((time.monotonic() - _t0) * 1000), _job_name)
            if _status_code is None:
                return None
            _cache_set("hist/strikes", params_key, cached, trade_date)
        else:
            from data.api_ledger import get_ledger
            get_ledger().record("orats_historical", "hist/strikes", symbol, True, None, None, _job_name)

        side = option_type.lower()
        for row in cached:
            row_strike = self._safe_float(row.get("strike"))
            row_exp = str(row.get("expirDate", ""))
            if row_strike is None:
                continue
            if abs(row_strike - target_strike) < 0.01 and row_exp == expiration_date:
                return self._project_single(row, side)

        return None

    # ── normalization helpers ────────────────────────────────────────────

    def _normalize_summary(self, row: dict) -> dict:
        sf = self._safe_float
        atm_m1 = sf(row.get("atmIvM1"))
        atm_m2 = sf(row.get("atmIvM2"))
        term_slope = None
        if atm_m1 is not None and atm_m2 is not None:
            term_slope = round(atm_m2 - atm_m1, 4)
        return {
            "ticker": str(row.get("ticker", "")).upper(),
            "trade_date": str(row.get("tradeDate", "")),
            "iv_rank_1y": sf(row.get("ivRank1y")),
            "iv_rank_1m": sf(row.get("ivRank1m")),
            "iv_pct_1y": sf(row.get("ivPct1y")),
            "atm_iv_m1": atm_m1,
            "atm_iv_m2": atm_m2,
            "atm_iv_m3": sf(row.get("atmIvM3")),
            "atm_iv_m4": sf(row.get("atmIvM4")),
            "skew_m1": sf(row.get("iSkewM1")),
            "skew_m2": sf(row.get("iSkewM2")),
            "implied_move_pct": sf(row.get("impliedMove")),
            "stock_price": sf(row.get("stockPrice") or row.get("stkPx")),
            "term_structure_slope": term_slope,
            "contango": sf(row.get("contango")),
            "rip": sf(row.get("rip")),
        }

    def _normalize_cores(self, row: dict) -> dict:
        sf = self._safe_float
        next_ern = row.get("nextErn")
        if next_ern in ("0000-00-00", ""):
            next_ern = None
        return {
            "next_earnings_date": next_ern,
            "days_to_next_earnings": row.get("daysToNextErn"),
            "hv_20d": sf(row.get("orHv20d")),
            "hv_30d": sf(row.get("orHv30d")),
            "iv_hv_ratio": sf(row.get("ivHvXernRatio")),
            "vol_of_vol": sf(row.get("volOfVol")),
            "skew_percentile": sf(row.get("slopepctile")),
        }

    def _project_strikes(self, rows: list[dict], side: str) -> list[dict]:
        return [self._project_single(r, side) for r in rows]

    def _project_single(self, row: dict, side: str) -> dict:
        sf = self._safe_float
        if side == "put":
            bid = sf(row.get("putBidPrice"))
            ask = sf(row.get("putAskPrice"))
            oi = row.get("putOpenInterest")
            delta = sf(row.get("delta"))
            if delta is not None:
                delta = -abs(delta)  # puts are negative
        else:
            bid = sf(row.get("callBidPrice"))
            ask = sf(row.get("callAskPrice"))
            oi = row.get("callOpenInterest")
            delta = sf(row.get("delta"))
            if delta is not None:
                delta = abs(delta)

        mid = None
        if bid is not None and ask is not None:
            mid = round((bid + ask) / 2, 4)

        return {
            "strike": sf(row.get("strike")),
            "expiration_date": str(row.get("expirDate", "")),
            "dte": row.get("dte"),
            "delta": delta,
            "theta": sf(row.get("theta")),
            "vega": sf(row.get("vega")),
            "gamma": sf(row.get("gamma")),
            "smv_vol": sf(row.get("smvVol")),
            "bid_price": bid,
            "ask_price": ask,
            "mid_price": mid,
            "open_interest": oi,
            "option_type": side,
        }

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) or math.isinf(f) else round(f, 4)
        except (TypeError, ValueError):
            return None
