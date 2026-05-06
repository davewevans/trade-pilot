"""SQLite-backed cache for ORATS API responses.

Uses the same database as the rest of trade-pilot (WAL mode).
Falls back gracefully to in-memory if DB is unavailable.
"""

import json
import logging
import sqlite3
import time
from typing import Union

logger = logging.getLogger(__name__)

_SINGLETON = object()  # sentinel: use the process-wide Database singleton


class ORATSCache:
    """SQLite-backed cache for ORATS API responses.

    Uses the same database as the rest of trade-pilot (WAL mode).
    Falls back gracefully to in-memory if DB is unavailable.

    Opens its own connection (separate from the main Database class)
    because the ORATS client can run in the scheduler process and we
    don't want to share connections across modules.
    """

    # Safety cap: prevent unbounded memory growth when running in fallback mode.
    _FALLBACK_MAX_ENTRIES = 500

    def __init__(self, conn: "sqlite3.Connection | None" = _SINGLETON):
        """Initialise the cache.

        Args:
            conn: Open sqlite3.Connection, or None for in-memory fallback, or omit
                  to use the process-wide Database singleton (production default).
                  The cache does NOT own this connection.
        """
        if conn is _SINGLETON:
            from database.db import get_db  # late import — avoids circular
            conn = get_db().get_connection()
        self._conn: sqlite3.Connection | None = conn
        self._fallback: dict[tuple[str, str], tuple[float, float, object]] = {}
        if self._conn is None:
            logger.critical(
                "ORATSCache: no connection — using in-memory fallback (DEGRADED MODE)"
            )

    # ── public API ──────────────────────────────────────────────────────────

    def get(
        self,
        endpoint: str,
        cache_key: str,
        ttl_seconds: float,
    ) -> Union[dict, list, None]:
        """Return cached data if it exists and hasn't expired. None on miss."""
        if not isinstance(endpoint, str) or not isinstance(cache_key, str):
            logger.warning(
                "ORATSCache.get rejected non-string params: endpoint=%r cache_key=%r",
                endpoint, cache_key,
            )
            return None

        if self._conn is None:
            return self._fallback_get(endpoint, cache_key, ttl_seconds)

        try:
            row = self._conn.execute(
                "SELECT data_json, fetched_at FROM orats_cache "
                "WHERE endpoint=? AND cache_key=?",
                (endpoint, cache_key),
            ).fetchone()
        except sqlite3.InterfaceError:
            logger.warning(
                "ORATSCache.get InterfaceError — param types: endpoint=%s cache_key=%s ttl=%s",
                type(endpoint).__name__, type(cache_key).__name__, type(ttl_seconds).__name__,
                exc_info=True,
            )
            return None
        except Exception:
            logger.warning("ORATSCache.get failed", exc_info=True)
            return None

        if row is None:
            return None

        data_json, fetched_at = row

        if fetched_at is None:
            logger.warning(
                "ORATSCache.get found NULL fetched_at for endpoint=%s cache_key=%s — treating as miss",
                endpoint, cache_key,
            )
            return None

        if (time.time() - fetched_at) >= ttl_seconds:
            return None

        try:
            return json.loads(data_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "ORATSCache: corrupt JSON for %s/%s — deleting row",
                endpoint, cache_key,
            )
            try:
                self._conn.execute(
                    "DELETE FROM orats_cache WHERE endpoint=? AND cache_key=?",
                    (endpoint, cache_key),
                )
                self._conn.commit()
            except Exception:
                pass
            return None

    def set(
        self,
        endpoint: str,
        cache_key: str,
        data: Union[dict, list],
        ttl_seconds: float,
    ) -> None:
        """Store data in cache. Uses INSERT OR REPLACE."""
        if not isinstance(endpoint, str) or not isinstance(cache_key, str):
            logger.warning(
                "ORATSCache.set rejected non-string params: endpoint=%r cache_key=%r",
                endpoint, cache_key,
            )
            return

        if self._conn is None:
            self._fallback_set(endpoint, cache_key, data, ttl_seconds)
            return

        ttl_int = int(ttl_seconds) if ttl_seconds is not None else 0

        try:
            self._conn.execute(
                """INSERT OR REPLACE INTO orats_cache
                   (endpoint, cache_key, data_json, fetched_at, ttl_seconds)
                   VALUES (?, ?, ?, ?, ?)""",
                (endpoint, cache_key, json.dumps(data, default=str), time.time(), ttl_int),
            )
            self._conn.commit()
        except sqlite3.InterfaceError:
            logger.warning(
                "ORATSCache.set InterfaceError — param types: endpoint=%s cache_key=%s "
                "data=%s ttl=%s",
                type(endpoint).__name__, type(cache_key).__name__,
                type(data).__name__, type(ttl_seconds).__name__,
                exc_info=True,
            )
        except Exception:
            logger.warning("ORATSCache.set failed", exc_info=True)

    def clear_expired(self) -> int:
        """Delete all rows where (now - fetched_at) > ttl_seconds. Return count deleted."""
        if self._conn is None:
            return self._fallback_clear_expired()

        try:
            cur = self._conn.execute(
                "DELETE FROM orats_cache WHERE (? - fetched_at) > ttl_seconds",
                (time.time(),),
            )
            self._conn.commit()
            return cur.rowcount
        except Exception:
            logger.warning("ORATSCache.clear_expired failed", exc_info=True)
            return 0

    def stats(self) -> dict:
        """Return cache statistics: total entries, expired entries, size by endpoint."""
        if self._conn is None:
            return {"backend": "in-memory", "total": len(self._fallback)}

        try:
            now = time.time()
            total = self._conn.execute(
                "SELECT COUNT(*) FROM orats_cache"
            ).fetchone()[0]
            expired = self._conn.execute(
                "SELECT COUNT(*) FROM orats_cache WHERE (? - fetched_at) > ttl_seconds",
                (now,),
            ).fetchone()[0]
            by_endpoint = {
                row[0]: row[1]
                for row in self._conn.execute(
                    "SELECT endpoint, COUNT(*) FROM orats_cache GROUP BY endpoint"
                ).fetchall()
            }
            return {
                "backend": "sqlite",
                "total": total,
                "expired": expired,
                "by_endpoint": by_endpoint,
            }
        except Exception:
            logger.warning("ORATSCache.stats failed", exc_info=True)
            return {"backend": "sqlite", "error": "stats unavailable"}

    # ── in-memory fallback ──────────────────────────────────────────────────

    def _fallback_get(self, endpoint, cache_key, ttl_seconds):
        entry = self._fallback.get((endpoint, cache_key))
        if entry is None:
            return None
        fetched_at, stored_ttl, data = entry
        if (time.time() - fetched_at) >= ttl_seconds:
            return None
        return data

    def _fallback_set(self, endpoint, cache_key, data, ttl_seconds):
        if len(self._fallback) >= self._FALLBACK_MAX_ENTRIES:
            logger.warning(
                "ORATSCache in-memory fallback is at capacity (%d entries) — dropping write for %s/%s",
                self._FALLBACK_MAX_ENTRIES, endpoint, cache_key,
            )
            return
        self._fallback[(endpoint, cache_key)] = (time.time(), ttl_seconds, data)

    def _fallback_clear_expired(self):
        now = time.time()
        expired_keys = [
            k for k, (fetched_at, ttl, _) in self._fallback.items()
            if (now - fetched_at) > ttl
        ]
        for k in expired_keys:
            del self._fallback[k]
        return len(expired_keys)
