"""Finnhub API client for earnings calendar data.

Primary source for earnings dates (more reliable than yfinance).
Free tier: 60 requests/minute.
"""

import logging
import time
from datetime import date
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

_EARNINGS_TTL = 6 * 3600
_earnings_cache: dict[str, tuple[float, dict]] = {}


class FinnhubClient:
    """Client for the Finnhub API."""

    BASE_URL = "https://finnhub.io/api/v1"
    TIMEOUT = 8

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.FINNHUB_API_KEY

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
            resp = requests.get(
                f"{self.BASE_URL}/calendar/earnings",
                params={"symbol": symbol, "token": self.api_key},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()

            events = payload.get("earningsCalendar", [])
            today = date.today()

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

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                logger.error("Finnhub API key is invalid")
            else:
                logger.warning("Finnhub earnings HTTP error for %s: %s", symbol, e)
        except Exception:
            logger.warning("Finnhub earnings failed for %s", symbol, exc_info=True)
        return None
