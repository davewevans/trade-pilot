"""End-to-end dry run — simulates one full market day without placing trades.

Run this before deploying to Render to confirm everything works
on your local machine:

    python scripts/test_full_run.py
"""

import logging
import os
import sys
import time

# Allow running from the scripts/ directory or project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("test-full-run")


def main() -> None:
    # ── Force dry-run mode ──────────────────────────────────
    from config import settings

    settings.DRY_RUN = True
    logger.info("DRY_RUN forced to True")
    logger.info("Watchlist: %s", settings.WATCHLIST)

    from jobs import (
        market_close,
        market_open,
        position_check,
        post_market,
        pre_close,
        pre_market,
    )

    jobs = [
        ("pre_market", pre_market.run),
        ("market_open", market_open.run),
        ("position_check", position_check.run),
        ("pre_close", pre_close.run),
        ("market_close", market_close.run),
        ("post_market", post_market.run),
    ]

    # ── Run each job ────────────────────────────────────────
    failures: list[str] = []

    for name, fn in jobs:
        logger.info("=" * 60)
        logger.info("  Running: %s", name)
        logger.info("=" * 60)
        try:
            fn()
        except Exception:
            logger.exception("FAILED: %s", name)
            failures.append(name)
        time.sleep(2)

    # ── Verification ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  VERIFICATION")
    print("=" * 60 + "\n")

    # a. Daily report
    from datetime import date

    today_path = settings.REPORTS_DIR / "daily" / f"{date.today().isoformat()}.md"
    if today_path.exists():
        print(f"✅ Daily report created: {today_path}")
    else:
        print(f"❌ Daily report NOT found at: {today_path}")

    # b. Positions report
    positions_path = settings.REPORTS_DIR / "positions" / "current.md"
    if positions_path.exists():
        print("✅ Positions report created")
    else:
        print("❌ Positions report NOT found")

    # c. Journal entries
    from data.trade_journal import TradeJournal

    journal = TradeJournal(path=settings.JOURNAL_PATH)
    entries = journal._read_all()
    if entries:
        print(f"✅ Journal has {len(entries)} entries")
    else:
        print("⚠️  Journal has 0 entries (expected if market was closed / no positions)")

    # d. Job failures
    if not failures:
        print("✅ All jobs completed without crashing")
    else:
        print(f"❌ {len(failures)} job(s) crashed: {', '.join(failures)}")

    # ── Preview daily report ────────────────────────────────
    if today_path.exists():
        print("\n" + "=" * 60)
        print("  DAILY REPORT PREVIEW (first 50 lines)")
        print("=" * 60 + "\n")
        with open(today_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 50:
                    print("... (truncated)")
                    break
                print(line, end="")

    # ── Summary ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  === Full dry run complete ===")
    print(f"  Daily report: {today_path}")
    print(f"  Journal entries: {len(entries)}")
    print(f"  Check logs at: {settings.LOG_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
