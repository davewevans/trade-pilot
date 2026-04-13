"""Weekly report job — runs Sunday at 6:00 PM ET."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


def run() -> None:
    """Generate a weekly summary from daily reports and journal entries."""
    logger.info("=== WEEKLY REPORT JOB STARTING ===")

    from data.trade_journal import TradeJournal

    journal = TradeJournal(path=settings.JOURNAL_PATH)
    now = datetime.now()
    week_start = (now - timedelta(days=7)).date()
    week_end = now.date()

    # ── Read journal entries for the week ───────────────────
    all_entries = journal._read_all()
    weekly_entries = []
    for e in all_entries:
        ts = e.get("timestamp", "")
        try:
            entry_date = datetime.fromisoformat(ts).date()
            if week_start <= entry_date <= week_end:
                weekly_entries.append(e)
        except (ValueError, TypeError):
            continue

    # ── Calculate metrics ───────────────────────────────────
    total_trades = len([e for e in weekly_entries if e.get("action") not in ("skip", "hold")])
    skips = len([e for e in weekly_entries if e.get("action") == "skip" or e.get("status") == "skipped"])

    closed = [e for e in weekly_entries if e.get("closed_at")]
    wins = len([e for e in closed if (e.get("pnl") or 0) > 0])
    losses = len([e for e in closed if (e.get("pnl") or 0) < 0])
    win_rate = (wins / len(closed) * 100) if closed else 0.0

    total_premium = sum(
        float(e.get("fill_price", 0)) * int(e.get("qty", 1)) * 100
        for e in weekly_entries
        if e.get("action") in ("sell_put", "sell_call") and e.get("fill_price")
    )

    total_pnl = sum(float(e.get("pnl", 0)) for e in weekly_entries if e.get("pnl"))

    # Average hold time (for closed entries with timestamps)
    hold_times: list[float] = []
    for e in closed:
        try:
            opened = datetime.fromisoformat(e["timestamp"])
            closed_at = datetime.fromisoformat(e["closed_at"])
            hold_times.append((closed_at - opened).total_seconds() / 86400)
        except (KeyError, ValueError, TypeError):
            continue
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0

    assignments = len([e for e in weekly_entries if e.get("status") == "assigned"])
    rolls = len([e for e in weekly_entries if e.get("action") == "roll"])

    # ── Claude vs pre-check value (S11) ─────────────────────
    # Decisions where the deterministic pre-check would have OPENed.
    pre_check_passes = [
        e for e in weekly_entries if e.get("pre_check_result") == "OPEN"
    ]
    claude_overrides = [
        e for e in pre_check_passes if e.get("claude_override")
    ]
    override_skips = [
        e for e in claude_overrides if (e.get("action") or "").lower() == "skip"
    ]
    claude_value_section = (
        f"\n## Claude Decision Value\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Pre-check passes (Claude could have opened) | "
        f"{len(pre_check_passes)} |\n"
        f"| Claude overrides (disagreed with pre-check) | "
        f"{len(claude_overrides)} |\n"
        f"| Override → SKIP (Claude vetoed an entry) | "
        f"{len(override_skips)} |\n"
    )

    # ── Collect daily reports ───────────────────────────────
    daily_dir = settings.REPORTS_DIR / "daily"
    daily_summaries: list[str] = []
    for day_offset in range(7, 0, -1):
        day = (now - timedelta(days=day_offset)).strftime("%Y-%m-%d")
        day_file = daily_dir / f"{day}.md"
        if day_file.exists():
            daily_summaries.append(f"- [{day}]({day_file.name})")

    # ── Generate weekly report ──────────────────────────────
    iso_year, iso_week, _ = now.isocalendar()
    report_name = f"{iso_year}-W{iso_week:02d}.md"
    report_path = settings.REPORTS_DIR / "weekly" / report_name

    report = f"""# Weekly Report — {iso_year}-W{iso_week:02d}

**Period:** {week_start} to {week_end}

## Metrics

| Metric | Value |
|--------|-------|
| Total trades | {total_trades} |
| Win rate | {win_rate:.1f}% ({wins}W / {losses}L) |
| Premium collected | ${total_premium:,.2f} |
| Realized P&L | ${total_pnl:,.2f} |
| Avg hold time | {avg_hold:.1f} days |
| Assignments | {assignments} |
| Rolls | {rolls} |
| Skips | {skips} |
{claude_value_section}
## Daily Reports

{chr(10).join(daily_summaries) if daily_summaries else "No daily reports found."}
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("Weekly report written: %s", report_path)

    # ── Update strategy comparison ──────────────────────────
    comparison_path = settings.REPORTS_DIR / "strategies" / "comparison.md"
    comparison_entry = (
        f"| {iso_year}-W{iso_week:02d} "
        f"| {total_trades} "
        f"| {win_rate:.1f}% "
        f"| ${total_premium:,.2f} "
        f"| ${total_pnl:,.2f} "
        f"| {avg_hold:.1f}d "
        f"| {assignments} "
        f"| {rolls} |\n"
    )

    if not comparison_path.exists():
        with open(comparison_path, "w", encoding="utf-8") as f:
            f.write("# Strategy Comparison — Weekly\n\n")
            f.write("| Week | Trades | Win Rate | Premium | P&L | Avg Hold | Assigns | Rolls |\n")
            f.write("|------|--------|----------|---------|-----|----------|---------|-------|\n")
            f.write(comparison_entry)
    else:
        with open(comparison_path, "a", encoding="utf-8") as f:
            f.write(comparison_entry)

    # ── Portfolio pattern analysis ──────────────────────────
    try:
        patterns = journal.get_portfolio_patterns(days=30)

        patterns_path = settings.SNAPSHOTS_DIR / "portfolio_patterns.json"
        patterns_path.parent.mkdir(parents=True, exist_ok=True)
        patterns_path.write_text(
            json.dumps(patterns, indent=2), encoding="utf-8"
        )
        logger.info("Portfolio patterns written to %s", patterns_path)
    except Exception:
        logger.exception("Failed to generate portfolio patterns")

    logger.info("=== WEEKLY REPORT JOB COMPLETE ===")
