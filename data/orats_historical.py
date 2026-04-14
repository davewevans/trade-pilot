"""ORATS historical data client with persistent SQLite caching.

Wraps the /hist/* endpoints of the ORATS Datav2 API.  Historical data
is immutable, so responses are cached indefinitely in a local SQLite DB.
A simple rate-limiter keeps requests under 1000/minute (ORATS limit).
"""

import json
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.orats.io/datav2"
_TIMEOUT = 15

# ── Rate limiter (1000 req / 60 s) ──────────────────────────────────────────

_RATE_LOCK = threading.Lock()
_CALL_TIMES: list[float] = []
_MAX_CALLS_PER_MINUTE = 900  # stay a little under the hard limit


def _rate_limit() -> None:
    """Block until we are below the per-minute call limit."""
    with _RATE_LOCK:
        now = time.monotonic()
        # Remove timestamps older than 60 s
        cutoff = now - 60.0
        while _CALL_TIMES and _CALL_TIMES[0] < cutoff:
            _CALL_TIMES.pop(0)

        if len(_CALL_TIMES) >= _MAX_CALLS_PER_MINUTE:
            # Wait until the oldest call falls off the window
            wait = 60.0 - (now - _CALL_TIMES[0]) + 0.05
            if wait > 0:
                logger.debug("ORATS historical rate limit — sleeping %.1fs", wait)
                time.sleep(wait)
            # Refresh after sleep
            now = time.monotonic()
            cutoff = now - 60.0
            while _CALL_TIMES and _CALL_TIMES[0] < cutoff:
                _CALL_TIMES.pop(0)

        _CALL_TIMES.append(time.monotonic())


# ── SQLite cache ─────────────────────────────────────────────────────────────

def _cache_db_path() -> Path:
    from config import settings as s
    p = s.DATA_DIR / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p / "orats_hist.db"


def _open_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_cache_db_path()), check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hist_cache (
            endpoint    TEXT NOT NULL,
            params_key  TEXT NOT NULL,
            data_json   TEXT NOT NULL,
            fetched_at  REAL NOT NULL,
            PRIMARY KEY (endpoint, params_key)
        )"""
    )
    conn.commit()
    return conn


_CACHE_CONN: sqlite3.Connection | None = None
_CACHE_LOCK = threading.Lock()


def _get_cache_conn() -> sqlite3.Connection:
    global _CACHE_CONN
    with _CACHE_LOCK:
        if _CACHE_CONN is None:
            _CACHE_CONN = _open_cache()
        return _CACHE_CONN


def _cache_get(endpoint: str, params_key: str) -> Optional[list | dict]:
    conn = _get_cache_conn()
    with _CACHE_LOCK:
        row = conn.execute(
            "SELECT data_json FROM hist_cache WHERE endpoint=? AND params_key=?",
            (endpoint, params_key),
        ).fetchone()
    if row:
        return json.loads(row[0])
    return None


def _cache_set(endpoint: str, params_key: str, data: list | dict) -> None:
    conn = _get_cache_conn()
    with _CACHE_LOCK:
        conn.execute(
            """INSERT OR REPLACE INTO hist_cache
               (endpoint, params_key, data_json, fetched_at) VALUES (?,?,?,?)""",
            (endpoint, params_key, json.dumps(data), time.time()),
        )
        conn.commit()


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
        cached = _cache_get("hist/strikes", params_key)
        if cached is not None:
            return self._project_strikes(cached, side)

        _rate_limit()
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
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS hist/strikes failed for %s on %s", symbol, trade_date,
                exc_info=True,
            )
            return []

        _cache_set("hist/strikes", params_key, rows)
        return self._project_strikes(rows, side)

    def get_summary_on_date(self, symbol: str, trade_date: str) -> Optional[dict]:
        """Fetch historical IV summary for a specific date.

        Returns a dict with iv_rank_1y, iv_rank_1m, atm_iv_m1, skew_m1, etc.
        or None on failure.
        """
        params_key = f"{symbol.upper()}|{trade_date}"
        cached = _cache_get("hist/summaries", params_key)
        if cached is not None:
            return cached[0] if cached else None

        _rate_limit()
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
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS hist/summaries failed for %s on %s", symbol, trade_date,
                exc_info=True,
            )
            return None

        _cache_set("hist/summaries", params_key, rows)
        if not rows:
            return None
        return self._normalize_summary(rows[0])

    def get_cores_on_date(self, symbol: str, trade_date: str) -> Optional[dict]:
        """Fetch historical /cores data for a specific date."""
        params_key = f"{symbol.upper()}|{trade_date}"
        cached = _cache_get("hist/cores", params_key)
        if cached is not None:
            return cached[0] if cached else None

        _rate_limit()
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
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS hist/cores failed for %s on %s", symbol, trade_date,
                exc_info=True,
            )
            return None

        _cache_set("hist/cores", params_key, rows)
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
        """
        params_key = f"{symbol.upper()}|{start_date or ''}|{end_date or ''}"
        cached = _cache_get("hist/ivrank", params_key)
        if cached is not None:
            return cached

        params: dict = {"token": self.api_key, "ticker": symbol.upper()}
        if start_date:
            params["tradeDate"] = f"{start_date},{end_date or ''}"

        _rate_limit()
        try:
            resp = requests.get(
                f"{BASE_URL}/hist/ivrank",
                params=params,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json().get("data", []) or []
        except Exception:
            logger.warning(
                "ORATS hist/ivrank failed for %s", symbol, exc_info=True,
            )
            return []

        _cache_set("hist/ivrank", params_key, rows)
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
        cached = _cache_get("hist/strikes", params_key)
        if cached is None:
            _rate_limit()
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
                cached = resp.json().get("data", []) or []
            except Exception:
                logger.warning(
                    "ORATS hist/strikes (wide) failed for %s on %s",
                    symbol, trade_date, exc_info=True,
                )
                return None
            _cache_set("hist/strikes", params_key, cached)

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
