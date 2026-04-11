"""Shared helpers for writing daily and weekly reports."""

from datetime import datetime

from config import settings

_DATE_FMT = "%Y-%m-%d"


def _today_str() -> str:
    return datetime.now().strftime(_DATE_FMT)


def daily_report_path() -> "Path":
    from pathlib import Path

    return settings.REPORTS_DIR / "daily" / f"{_today_str()}.md"


def append_section(title: str, body: str) -> None:
    """Append a titled section to today's daily report."""
    path = daily_report_path()
    is_new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Daily Report — {_today_str()}\n\n")
        f.write(f"## {title}\n\n{body}\n\n")
