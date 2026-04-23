"""Structured log handler that tees WARNING/ERROR log records to a rolling
JSONL file on persistent disk.

Used by the daily_bundle endpoint to surface errors + tracebacks grouped by
pattern. Works alongside existing stderr logging — does not replace it.
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import date, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class JSONLStructuredHandler(TimedRotatingFileHandler):
    """Write log records as one JSON object per line, rotated daily.

    Each record:
      {
        "ts": ISO8601 with timezone,
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
        min_level: int = logging.WARNING,
        backup_count: int = 30,
    ):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        filename = str(self._log_dir / f"{date.today().isoformat()}.jsonl")
        super().__init__(
            filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            utc=False,
            encoding="utf-8",
        )
        self.setLevel(min_level)
        self.suffix = "%Y-%m-%d.jsonl"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = {
                "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(),
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
                    try:
                        json.dumps(v, default=str)
                        payload["extra"][k] = v
                    except (TypeError, ValueError):
                        payload["extra"][k] = str(v)
            line = json.dumps(payload, default=str) + "\n"
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(line)
            self.stream.flush()
        except Exception:
            self.handleError(record)


def install_structured_log_handler(
    log_dir: str | Path,
    min_level: int = logging.WARNING,
    backup_count: int = 30,
) -> JSONLStructuredHandler:
    """Install the handler on the root logger. Idempotent — returns existing handler if already installed."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, JSONLStructuredHandler):
            return h
    handler = JSONLStructuredHandler(log_dir, min_level, backup_count)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    return handler
