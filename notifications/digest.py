"""
JSONL accumulator for notification digests.
Future daily/weekly email digest job will consume data/notifications_pending.jsonl.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_path() -> Path:
    from config import settings
    return settings.DATA_DIR / "notifications_pending.jsonl"


def append(severity: str, title: str, message: str, tags: list[str]) -> None:
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "severity": severity,
        "title": title,
        "message": message,
        "tags": tags,
    }
    path = _get_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_pending(since: datetime) -> list[dict]:
    path = _get_path()
    if not path.exists():
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry["timestamp"])
                if ts >= since:
                    results.append(entry)
            except Exception:
                continue
    return results
