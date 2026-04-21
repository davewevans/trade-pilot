"""Startup snapshot job — runs once on boot.

Fetches live account data for every Alpaca account and writes initial
per-account portfolio snapshots so the dashboard has data immediately,
before any trading cycle has run.
"""

import json
import logging
from datetime import datetime, timezone

from brokers.broker_factory import make_broker_cached
from config import settings
from data.account_manager import AccountManager
from data.state_writer import StateWriter

logger = logging.getLogger(__name__)

# Built dynamically from AccountManager so expiry_guard / portfolio_refresh
# can still import this name while their own migrations are pending.
def _build_account_broker_map() -> dict[str, str]:
    manager = AccountManager()
    return {
        account_id: acct["strategy"]
        for account_id, acct in manager.get_all_accounts().items()
        if acct.get("strategy")
    }

ACCOUNT_BROKER_MAP: dict[str, str] = _build_account_broker_map()

# Sonnet 4.6 context window size (tokens)
_CONTEXT_WINDOW = 200_000


def _write_prompt_size_snapshot() -> None:
    """Measure system prompt token count and write prompt_size.json."""
    try:
        from ai.claude_advisor import ClaudeAdvisor
        advisor = ClaudeAdvisor()
        prompt_tokens = advisor.count_system_prompt_tokens()
        snapshot = {
            "system_prompt_tokens": prompt_tokens,
            "model": advisor.model,
            "context_window": _CONTEXT_WINDOW,
            "utilization_pct": (
                round(prompt_tokens / _CONTEXT_WINDOW * 100, 2)
                if prompt_tokens is not None else None
            ),
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        out_path = settings.SNAPSHOTS_DIR / "prompt_size.json"
        out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        logger.info(
            "Prompt size snapshot written: %s tokens (%.2f%% of context window)",
            prompt_tokens,
            snapshot["utilization_pct"] or 0,
        )
    except Exception as e:
        logger.warning("Failed to write prompt_size snapshot: %s", e)


def run() -> None:
    """Write initial portfolio snapshots for all accounts."""
    logger.info("=== STARTUP SNAPSHOT ===")
    sw = StateWriter()
    manager = AccountManager()

    for account_id in manager.get_all_accounts():
        try:
            broker = make_broker_cached(*manager.get_credentials(account_id))
            account = broker.get_account()
            positions = broker.get_positions()
            sw.write_account_snapshot(account_id, account, positions)
            logger.info(
                "Startup snapshot written for %s: buying_power=%s",
                account_id,
                account.get("buying_power"),
            )
        except Exception as e:
            logger.warning(
                "Failed to write startup snapshot for %s: %s",
                account_id, e,
            )

    _write_prompt_size_snapshot()
