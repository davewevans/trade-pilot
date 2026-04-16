"""Open interest cache.

Alpaca's options snapshot endpoint does not return open interest.
OI lives on the OptionContract model from /v2/options/contracts and is
only updated end-of-day. We cache per-underlying for the trading day
and refresh in the pre_market job.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus

logger = logging.getLogger(__name__)

# OI updates once per day after market close. 6h TTL means pre_market
# refresh always wins and intraday cycles never re-fetch.
_DEFAULT_TTL = timedelta(hours=6)


@dataclass
class _CacheEntry:
    fetched_at: datetime
    oi_by_symbol: Dict[str, int] = field(default_factory=dict)


class OpenInterestCache:
    """Per-underlying OI cache. Thread-safe."""

    def __init__(self, trading_client, ttl: timedelta = _DEFAULT_TTL):
        self._client = trading_client
        self._ttl = ttl
        self._cache: Dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, occ_symbol: str, underlying: str) -> Optional[int]:
        """Return OI for a single OCC contract symbol, fetching if needed."""
        return self._get_or_fetch(underlying).get(occ_symbol)

    def get_map(self, underlying: str) -> Dict[str, int]:
        """Return the full {OCC symbol: OI} map for an underlying."""
        return self._get_or_fetch(underlying)

    def refresh(self, underlying: str) -> None:
        """Force a refresh. Call from the pre_market job."""
        with self._lock:
            self._cache.pop(underlying, None)
        self._get_or_fetch(underlying)

    def _get_or_fetch(self, underlying: str) -> Dict[str, int]:
        with self._lock:
            entry = self._cache.get(underlying)
            now = datetime.now(timezone.utc)
            if entry and (now - entry.fetched_at) < self._ttl:
                return entry.oi_by_symbol

        # fetch outside the lock to avoid holding it across a network call
        oi_map = self._fetch(underlying)

        with self._lock:
            self._cache[underlying] = _CacheEntry(
                fetched_at=datetime.now(timezone.utc),
                oi_by_symbol=oi_map,
            )
        return oi_map

    def _fetch(self, underlying: str) -> Dict[str, int]:
        """Page through all active contracts for the underlying."""
        oi_map: Dict[str, int] = {}
        page_token: Optional[str] = None
        pages = 0

        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying],
                status=AssetStatus.ACTIVE,
                limit=10000,
                page_token=page_token,
            )
            try:
                resp = self._client.get_option_contracts(req)
            except Exception as exc:
                logger.error(
                    "OI fetch failed for %s on page %d: %s",
                    underlying, pages, exc,
                )
                break

            for c in resp.option_contracts or []:
                if c.open_interest is None:
                    continue
                try:
                    oi_map[c.symbol] = int(c.open_interest)
                except (TypeError, ValueError):
                    continue

            # Page token field name varies by alpaca-py version. Check both.
            page_token = getattr(resp, "next_page_token", None) or getattr(resp, "page_token", None)
            pages += 1
            if not page_token or pages >= 20:  # safety cap
                break

        logger.info(
            "OI cache: fetched %d contracts for %s across %d page(s)",
            len(oi_map), underlying, pages,
        )
        return oi_map
