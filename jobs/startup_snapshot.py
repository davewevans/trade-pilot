"""Startup snapshot job — runs once on boot.

Fetches live account data for every Alpaca account and writes initial
per-account portfolio snapshots so the dashboard has data immediately,
before any trading cycle has run.
"""

import json
import logging
from datetime import datetime, timezone

from brokers.broker_factory import make_broker
from config import settings
from data.state_writer import StateWriter

logger = logging.getLogger(__name__)

ACCOUNT_NAMES = ["wheel", "iron_condor", "spreads"]

# "spreads" account uses the default Alpaca credentials.
# make_broker() uses STRATEGY_ACCOUNT_MAP — map "spreads" to the
# strategies that share the default account (bull_put_spread works).
ACCOUNT_BROKER_MAP = {
    "wheel":       "wheel",
    "iron_condor": "iron_condor",
    "spreads":     "bull_put_spread",
}

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

    for account_name, strategy_key in ACCOUNT_BROKER_MAP.items():
        try:
            broker = make_broker(strategy_key)
            account = broker.get_account()
            positions = broker.get_positions()
            sw.write_account_snapshot(account_name, account, positions)
            logger.info(
                "Startup snapshot written for %s: buying_power=%s",
                account_name,
                account.get("buying_power"),
            )
        except Exception as e:
            logger.warning(
                "Failed to write startup snapshot for %s: %s",
                account_name, e,
            )

    _write_prompt_size_snapshot()
