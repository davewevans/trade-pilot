"""API usage ledger — records every outbound API call in SQLite and
enforces hard monthly/daily/per-minute caps before each ORATS request.

Design decisions
~~~~~~~~~~~~~~~~
- **Month window**: starts at 00:00 UTC on the 1st of the current calendar
  month.  Resets cleanly when the month rolls over.
- **Day window**: *rolling* 24 hours (not a calendar day).  Chosen because
  the weekly_research sweep is bursty and can finish in a single overnight
  run; a strict "today" window would block a sweep that started before
  midnight and ran into the next day.
- **Minute window**: rolling 60 seconds — matches the rate-limiter semantics
  of the old ``_CALL_TIMES`` deque.

Only ``orats_historical`` and ``orats_live`` are cap-enforced.  Other APIs
(``anthropic``, ``alpaca``) are recorded for observability but never blocked.

Thread safety
~~~~~~~~~~~~~
``check_and_reserve`` is serialised by a per-instance ``threading.Lock`` so
that concurrent threads in the same process can't both slip through a cap
check at the last call before the limit.  Between separate processes, SQLite's
WAL mode serialises writes; the soft-enforcement race window is sub-millisecond
and acceptable for this use-case.
"""

import atexit
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class OratsQuotaExceeded(Exception):
    """Raised when an ORATS cap (monthly / daily / per-minute) would be exceeded.

    Attributes:
        reason:  One of ``'monthly_cap'``, ``'daily_cap'``, ``'minute_cap'``.
        usage:   Snapshot dict from :meth:`ApiLedger.get_usage` at the time of
                 the block, plus ``api`` and ``endpoint`` keys.
    """

    def __init__(self, reason: str, usage: dict) -> None:
        super().__init__(f"ORATS quota exceeded: {reason} — {usage}")
        self.reason = reason
        self.usage = usage


class ApiLedger:
    """Per-process SQLite ledger for API call tracking and cap enforcement.

    Args:
        db_path: Path to the SQLite database (same file as the main DB).
    """

    # How many billable (non-cache, non-blocked) calls between auto-snapshots.
    _SNAPSHOT_INTERVAL = 50

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.commit()
        self._lock = threading.RLock()  # RLock so check_and_reserve can call _insert while holding it
        self._billable_since_snapshot = 0
        self._init_schema()
        atexit.register(self._atexit_snapshot)

    # ── public API ────────────────────────────────────────────────────────

    def check_and_reserve(
        self,
        api: str,
        endpoint: str,
        symbol: Optional[str],
        job_name: Optional[str],
    ) -> None:
        """Atomically check all caps and raise if any would be exceeded.

        Does NOT insert a row — the caller inserts via :meth:`record` after
        the HTTP call completes so we can capture status_code and duration.

        If a cap is exceeded, inserts a blocked row (for audit purposes) and
        raises :class:`OratsQuotaExceeded`.

        Cache hits must call :meth:`record` directly (with ``cache_hit=True``)
        without calling this method.

        Args:
            api:       ``'orats_historical'``, ``'orats_live'``, etc.
            endpoint:  e.g. ``'hist/summaries'``, ``'summaries'``.
            symbol:    Underlying ticker, or None.
            job_name:  Value of ``TRADE_PILOT_JOB_NAME`` env var.
        """
        caps = self._caps_for(api)
        if caps is None:
            return  # not a capped API

        quota_exc: Optional[OratsQuotaExceeded] = None
        with self._lock:
            now = time.time()
            month_used = self._count_window(api, self._month_start_ts(now))
            day_used = self._count_window(api, now - 86400.0)
            minute_used = self._count_window(api, now - 60.0)

            for reason, used, cap in (
                ("monthly_cap", month_used, caps["monthly"]),
                ("daily_cap", day_used, caps["daily"]),
                ("minute_cap", minute_used, caps["minute"]),
            ):
                if used >= cap:
                    usage = {
                        "api": api,
                        "endpoint": endpoint,
                        "month_used": month_used,
                        "month_cap": caps["monthly"],
                        "day_used": day_used,
                        "day_cap": caps["daily"],
                        "minute_used": minute_used,
                        "minute_cap": caps["minute"],
                    }
                    self._insert(
                        api=api,
                        endpoint=endpoint,
                        ts=now,
                        symbol=symbol,
                        cache_hit=False,
                        status_code=None,
                        duration_ms=None,
                        job_name=job_name,
                        blocked_reason=reason,
                    )
                    self._emit_api_log(api, endpoint, symbol, False, None, None, reason, job_name)
                    quota_exc = OratsQuotaExceeded(reason=reason, usage=usage)
                    break  # only raise on the first exceeded cap

        # Snapshot and raise outside the lock
        if quota_exc is not None:
            self.write_snapshot()
            raise quota_exc

    # ── structured api_calls log ──────────────────────────────────────────

    def _emit_api_log(
        self,
        api: str,
        endpoint: str,
        symbol: Optional[str],
        cache_hit: bool,
        status_code: Optional[int],
        duration_ms: Optional[int],
        blocked_reason: Optional[str],
        job_name: Optional[str],
    ) -> None:
        """Emit one line to the api_calls structured logger.

        Safe to call even if the logger has no handlers (NullHandler default).
        Never raises.
        """
        try:
            _api_log = logging.getLogger("api_calls")
            if _api_log.hasHandlers() or _api_log.propagate:
                _api_log.info(
                    "",
                    extra={
                        "api": api,
                        "endpoint": endpoint,
                        "symbol": symbol,
                        "status": status_code,
                        "cache_hit": cache_hit,
                        "duration_ms": duration_ms,
                        "blocked_reason": blocked_reason,
                        "pid": os.getpid(),
                        "job_name": job_name,
                    },
                )
        except Exception:
            pass  # logging failure must never affect the caller

    def record(
        self,
        api: str,
        endpoint: str,
        symbol: Optional[str],
        cache_hit: bool,
        status_code: Optional[int],
        duration_ms: Optional[int],
        job_name: Optional[str],
    ) -> None:
        """Insert one ledger row for a completed (or cache-hit) API call.

        Cache hits (``cache_hit=True``) do not consume budget and are not
        counted by :meth:`check_and_reserve` window queries.

        Args:
            api:         e.g. ``'orats_historical'``.
            endpoint:    e.g. ``'hist/summaries'``.
            symbol:      Underlying ticker or None.
            cache_hit:   True if served from local cache (no HTTP request made).
            status_code: HTTP status code; None for cache hits or blocked calls.
            duration_ms: Request round-trip in ms; None for cache hits or blocks.
            job_name:    From ``TRADE_PILOT_JOB_NAME`` env var.
        """
        with self._lock:
            self._insert(
                api=api,
                endpoint=endpoint,
                ts=time.time(),
                symbol=symbol,
                cache_hit=cache_hit,
                status_code=status_code,
                duration_ms=duration_ms,
                job_name=job_name,
                blocked_reason=None,
            )
            # Count only real billable calls toward the snapshot trigger
            if not cache_hit and self._caps_for(api) is not None:
                self._billable_since_snapshot += 1
                if self._billable_since_snapshot >= self._SNAPSHOT_INTERVAL:
                    self._billable_since_snapshot = 0
                    do_snapshot = True
                else:
                    do_snapshot = False
            else:
                do_snapshot = False

        self._emit_api_log(api, endpoint, symbol, cache_hit, status_code, duration_ms, None, job_name)
        if do_snapshot:
            self.write_snapshot()

    def get_usage(self, api: str) -> dict:
        """Return current usage counters and caps for *api*.

        Returns a dict with keys:
            ``month_used``, ``month_cap``, ``month_remaining``,
            ``day_used``,   ``day_cap``,   ``day_remaining``,
            ``minute_used``, ``minute_cap``, ``minute_remaining``.

        Cap values are read from ``settings`` at call time so they reflect
        any in-process overrides (useful in tests).
        """
        caps = self._caps_for(api) or {"monthly": 0, "daily": 0, "minute": 0}
        now = time.time()
        month_used = self._count_window(api, self._month_start_ts(now))
        day_used = self._count_window(api, now - 86400.0)
        minute_used = self._count_window(api, now - 60.0)
        return {
            "month_used": month_used,
            "month_cap": caps["monthly"],
            "month_remaining": max(0, caps["monthly"] - month_used),
            "day_used": day_used,
            "day_cap": caps["daily"],
            "day_remaining": max(0, caps["daily"] - day_used),
            "minute_used": minute_used,
            "minute_cap": caps["minute"],
            "minute_remaining": max(0, caps["minute"] - minute_used),
        }

    def write_snapshot(self) -> None:
        """Write current usage for all capped APIs to data/snapshots/api_usage.json.

        Atomic write (tmp → replace) so the API server never reads a partial file.
        Safe to call from any thread; never raises.
        """
        try:
            from config import settings  # late import — avoids circular dep

            snapshot = {
                "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "orats_historical": self.get_usage("orats_historical"),
                "orats_live": self.get_usage("orats_live"),
            }
            snapshots_dir = settings.SNAPSHOTS_DIR
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            out_path = snapshots_dir / "api_usage.json"
            tmp_path = out_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            os.replace(str(tmp_path), str(out_path))
        except Exception:
            logger.warning("ApiLedger.write_snapshot failed", exc_info=True)

    def _atexit_snapshot(self) -> None:
        """Write a final snapshot on process exit (atexit hook)."""
        self.write_snapshot()

    # ── internals ─────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Create the ledger table if it doesn't exist (belt-and-suspenders;
        the main schema init in database/db.py also creates it on startup)."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_usage_ledger (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                api           TEXT    NOT NULL,
                endpoint      TEXT    NOT NULL,
                ts            REAL    NOT NULL,
                symbol        TEXT,
                cache_hit     INTEGER NOT NULL,
                status_code   INTEGER,
                duration_ms   INTEGER,
                process_id    INTEGER NOT NULL,
                job_name      TEXT,
                blocked_reason TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ledger_api_ts ON api_usage_ledger(api, ts)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ledger_ts ON api_usage_ledger(ts)"
        )
        self._conn.commit()

    def _insert(
        self,
        api: str,
        endpoint: str,
        ts: float,
        symbol: Optional[str],
        cache_hit: bool,
        status_code: Optional[int],
        duration_ms: Optional[int],
        job_name: Optional[str],
        blocked_reason: Optional[str],
    ) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO api_usage_ledger
                    (api, endpoint, ts, symbol, cache_hit,
                     status_code, duration_ms, process_id, job_name, blocked_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    api,
                    endpoint,
                    ts,
                    symbol,
                    1 if cache_hit else 0,
                    status_code,
                    duration_ms,
                    os.getpid(),
                    job_name,
                    blocked_reason,
                ),
            )
            self._conn.commit()
        except Exception:
            logger.warning("ApiLedger._insert failed", exc_info=True)

    def _count_window(self, api: str, since: float) -> int:
        """Count billable (non-cache-hit, non-blocked) calls for *api* since *since*."""
        row = self._conn.execute(
            """
            SELECT COUNT(*) FROM api_usage_ledger
            WHERE api = ?
              AND cache_hit = 0
              AND blocked_reason IS NULL
              AND ts >= ?
            """,
            (api, since),
        ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _month_start_ts(now: float) -> float:
        """Return the epoch timestamp of 00:00 UTC on the 1st of the current month."""
        dt = datetime.fromtimestamp(now, tz=timezone.utc)
        month_start = datetime(dt.year, dt.month, 1, 0, 0, 0, tzinfo=timezone.utc)
        return month_start.timestamp()

    def _caps_for(self, api: str) -> Optional[dict]:
        """Return {monthly, daily, minute} caps for *api*, or None if not capped."""
        from config import settings  # late import to avoid circular dependency

        if api == "orats_historical":
            return {
                "monthly": settings.ORATS_HISTORICAL_MONTHLY_CAP,
                "daily": settings.ORATS_HISTORICAL_DAILY_CAP,
                "minute": settings.ORATS_HISTORICAL_MINUTE_CAP,
            }
        if api == "orats_live":
            return {
                "monthly": settings.ORATS_LIVE_MONTHLY_CAP,
                "daily": settings.ORATS_LIVE_DAILY_CAP,
                "minute": settings.ORATS_LIVE_MINUTE_CAP,
            }
        return None  # anthropic, alpaca, etc. — record but don't cap


# ── process-wide singleton ────────────────────────────────────────────────────

_ledger: Optional[ApiLedger] = None
_ledger_init_lock = threading.Lock()


def get_ledger() -> ApiLedger:
    """Return the process-wide ApiLedger singleton (lazy init)."""
    global _ledger
    if _ledger is None:
        with _ledger_init_lock:
            if _ledger is None:
                from config import settings  # late import

                _ledger = ApiLedger(settings.DATABASE_PATH)
    return _ledger


def _set_ledger_for_testing(ledger: Optional["ApiLedger"]) -> None:
    """Replace (or clear) the process singleton for tests. Not for production use."""
    global _ledger
    _ledger = ledger
