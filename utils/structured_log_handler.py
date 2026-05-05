"""Structured log handler that tees WARNING/ERROR log records to a rolling
JSONL file on persistent disk.

Used by the daily_bundle endpoint to surface errors + tracebacks grouped by
pattern. Works alongside existing stderr logging — does not replace it.
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_log = logging.getLogger(__name__)


class JSONLStructuredHandler(logging.FileHandler):
    """Write log records as one JSON object per line to {log_dir}/{YYYY-MM-DD}.jsonl.

    Re-opens to a new date-named file whenever the calendar date (in ET)
    changes. This replaces a prior TimedRotatingFileHandler-based
    implementation that suffered from a static baseFilename bug: after
    midnight rollover, TRFH kept writing to the process-start-date file
    forever instead of opening today's file.

    Each record:
      {
        "ts": ISO8601 with timezone offset (ET),
        "level": "WARNING" | "ERROR" | "CRITICAL",
        "logger": dotted logger name,
        "message": formatted message,
        "traceback": full traceback string if record has exc_info, else None,
        "extra": dict of any non-standard LogRecord attributes (extra=...),
      }
    """

    _STANDARD_ATTRS = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "taskName",
    })

    def __init__(
        self,
        log_dir: str | Path,
        level: int = logging.WARNING,
        tz: ZoneInfo = _ET,
    ):
        self._log_dir = Path(log_dir).resolve()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._tz = tz
        self._current_date = self._today()
        super().__init__(
            filename=self._filename_for(self._current_date),
            mode="a",
            encoding="utf-8",
            delay=False,
        )
        self.setLevel(level)

    def _today(self) -> date:
        return datetime.now(self._tz).date()

    def _filename_for(self, d: date) -> str:
        return str(self._log_dir / f"{d.isoformat()}.jsonl")

    def emit(self, record: logging.LogRecord) -> None:
        # Handler.handle() acquires self.lock before calling emit(),
        # so the date-change re-open below is thread-safe.
        today = self._today()
        if today != self._current_date:
            self._current_date = today
            try:
                self.close()
            except Exception:
                pass
            self.baseFilename = self._filename_for(today)
            self.stream = self._open()

        try:
            payload: dict = {
                "ts": datetime.fromtimestamp(record.created, tz=self._tz).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "traceback": None,
                "extra": {},
            }
            if record.exc_info:
                payload["traceback"] = "".join(
                    traceback.format_exception(*record.exc_info)
                )
            for k, v in record.__dict__.items():
                if k not in self._STANDARD_ATTRS:
                    payload["extra"][k] = v
            line = json.dumps(payload, default=str) + "\n"
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(line)
            self.stream.flush()
        except Exception:
            self.handleError(record)


def _purge_old_logs(log_dir: Path, retention_days: int) -> None:
    """Delete *.jsonl files older than retention_days calendar days (ET)."""
    cutoff = datetime.now(_ET).date() - timedelta(days=retention_days)
    for path in log_dir.glob("*.jsonl"):
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if file_date < cutoff:
            path.unlink(missing_ok=True)


def install_structured_log_handler(
    log_dir: str | Path,
    min_level: int = logging.WARNING,
    retention_days: int = 30,
) -> JSONLStructuredHandler:
    """Install the handler on the root logger. Idempotent — returns existing handler if already installed."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, JSONLStructuredHandler):
            _log.info("Structured log handler already installed at %s", h._log_dir)
            return h
    handler = JSONLStructuredHandler(log_dir, level=min_level)
    root.addHandler(handler)
    _purge_old_logs(Path(log_dir), retention_days)
    _log.info(
        "Structured log handler installed: dir=%s min_level=%s",
        Path(log_dir).resolve(), logging.getLevelName(min_level),
    )
    return handler
