"""Tracks success/failure of each data source over time.

Persists state to a JSON file so health is visible across cycles.
Writes are atomic (temp-file + os.replace) to avoid partial reads.
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path

from config import settings


class SourceHealth:
    """Tracks success/failure of each data source over time."""

    def __init__(self, state_path: Path | None = None):
        self._path = state_path or settings.SNAPSHOTS_DIR / "source_health.json"
        self._sources: dict = {}
        self._load()

    def record(self, source_name: str, success: bool, detail: str = "") -> None:
        """Record a fetch attempt for a data source."""
        now = datetime.now().isoformat(timespec="seconds")
        if source_name not in self._sources:
            self._sources[source_name] = {
                "last_success": None,
                "last_failure": None,
                "last_failure_reason": None,
                "consecutive_failures": 0,
                "today_successes": 0,
                "today_failures": 0,
                "last_checked": None,
            }
        entry = self._sources[source_name]
        entry["last_checked"] = now
        if success:
            entry["last_success"] = now
            entry["consecutive_failures"] = 0
            entry["today_successes"] += 1
        else:
            entry["last_failure"] = now
            entry["last_failure_reason"] = detail[:200]
            entry["consecutive_failures"] += 1
            entry["today_failures"] += 1
        self._save()

    def get_all(self) -> dict:
        return dict(self._sources)

    def reset_daily_counts(self) -> None:
        for s in self._sources.values():
            s["today_successes"] = 0
            s["today_failures"] = 0
        self._save()

    # ── persistence ─────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._path.exists():
                import json
                self._sources = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._sources = {}

    def _save(self) -> None:
        import json
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".source_health_", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(self._sources, f, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
