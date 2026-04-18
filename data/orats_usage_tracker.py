"""Tracks ORATS API call counts across research job runs."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

class ORATSUsageTracker:
    def __init__(self, path: str | None = None):
        if path is None:
            from config import settings
            path = str(settings.DATA_DIR / "orats_usage.jsonl")
        self.path = Path(path)

    def record_run(
        self,
        run_type: str,
        call_count: int,
        by_endpoint: dict,
        started_at: datetime,
        completed_at: datetime,
    ) -> None:
        """Append a usage record to the JSONL log file."""
        entry = {
            "run_type": run_type,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": (completed_at - started_at).total_seconds(),
            "call_count": call_count,
            "by_endpoint": by_endpoint,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def get_recent_runs(self, since_days: int = 30) -> list[dict]:
        """Return runs started within the past since_days days, newest first."""
        if not self.path.exists():
            return []
        cutoff = datetime.utcnow() - timedelta(days=since_days)
        runs = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    started = datetime.fromisoformat(entry["started_at"])
                    if started >= cutoff:
                        runs.append(entry)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return sorted(runs, key=lambda r: r["started_at"], reverse=True)

    def get_daily_totals(self, since_days: int = 30) -> list[dict]:
        """Aggregate call counts per calendar day, oldest first."""
        runs = self.get_recent_runs(since_days)
        daily: dict[str, int] = {}
        for run in runs:
            date = run["started_at"][:10]
            daily[date] = daily.get(date, 0) + run["call_count"]
        return [{"date": d, "total_calls": c} for d, c in sorted(daily.items())]
