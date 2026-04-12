"""Startup snapshot job — runs once on boot.

Fetches live account data for every Alpaca account and writes initial
per-account portfolio snapshots so the dashboard has data immediately,
before any trading cycle has run.
"""

import logging

from brokers.broker_factory import make_broker
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
