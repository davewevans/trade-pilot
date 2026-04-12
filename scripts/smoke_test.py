"""Smoke test that exercises every AlpacaBroker and market data method against paper."""

import logging
import sys
import os

# Allow running from the scripts/ directory or project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("smoke-test")


def section(title: str) -> None:
    logger.info("=" * 60)
    logger.info("  %s", title)
    logger.info("=" * 60)


def main() -> None:
    from brokers.alpaca_broker import AlpacaBroker
    from data.market_data import get_option_snapshot, get_option_chain

    broker = AlpacaBroker()
    contract_symbol = None

    # ── 1. get_account ───────────────────────────────────────
    section("1. get_account()")
    try:
        account = broker.get_account()
        logger.info("Buying power:          %s", account.get("buying_power"))
        logger.info("Options approved level: %s", account.get("options_approved_level"))
        logger.info("Options trading level:  %s", account.get("options_trading_level"))
    except Exception:
        logger.exception("get_account FAILED")

    # ── 2. get_option_contracts ──────────────────────────────
    section("2. get_option_contracts('SPY', option_type='call')")
    try:
        contracts = broker.get_option_contracts("SPY", option_type="call")
        for c in contracts[:3]:
            logger.info(
                "  %s | strike=%s exp=%s tradable=%s",
                c["symbol"], c["strike_price"], c["expiration_date"], c["tradable"],
            )
        if contracts:
            contract_symbol = contracts[0]["symbol"]
            logger.info("Using contract symbol for next tests: %s", contract_symbol)
        else:
            logger.warning("No contracts returned — subsequent tests may fail")
    except Exception:
        logger.exception("get_option_contracts FAILED")

    # ── 3. get_option_contract ───────────────────────────────
    section("3. get_option_contract()")
    if contract_symbol:
        try:
            detail = broker.get_option_contract(contract_symbol)
            logger.info("  id=%s  name=%s  strike=%s", detail.get("id"), detail.get("name"), detail.get("strike_price"))
        except Exception:
            logger.exception("get_option_contract FAILED")
    else:
        logger.warning("Skipped — no contract symbol from step 2")

    # ── 4. get_option_snapshot (market data) ─────────────────
    section("4. get_option_snapshot()")
    if contract_symbol:
        try:
            snapshots = get_option_snapshot([contract_symbol])
            for sym, snap in snapshots.items():
                logger.info("  %s", sym)
                logger.info("    IV:     %s", snap.get("implied_volatility"))
                logger.info("    Greeks: %s", snap.get("greeks"))
                quote = snap.get("latest_quote", {})
                logger.info("    Bid=%s  Ask=%s", quote.get("bid_price"), quote.get("ask_price"))
        except Exception:
            logger.exception("get_option_snapshot FAILED")
    else:
        logger.warning("Skipped — no contract symbol from step 2")

    # ── 5. get_option_chain (market data) ────────────────────
    section("5. get_option_chain('SPY')")
    try:
        chain = get_option_chain("SPY")
        logger.info("Contracts in chain: %d", len(chain))
    except Exception:
        logger.exception("get_option_chain FAILED")

    # ── 6. get_orders ────────────────────────────────────────
    section("6. get_orders()")
    try:
        orders = broker.get_orders(status="open")
        logger.info("Open option orders: %d", len(orders))
        for o in orders[:3]:
            logger.info("  id=%s symbol=%s side=%s status=%s", o.get("id"), o.get("symbol"), o.get("side"), o.get("status"))
    except Exception:
        logger.exception("get_orders FAILED")

    # ── 7. get_positions ─────────────────────────────────────
    section("7. get_positions()")
    try:
        positions = broker.get_positions()
        logger.info("Open option positions: %d", len(positions))
        for p in positions[:3]:
            logger.info("  symbol=%s qty=%s avg_entry=%s", p.get("symbol"), p.get("qty"), p.get("avg_entry_price"))
    except Exception:
        logger.exception("get_positions FAILED")

    # ── 8. get_account_activities ────────────────────────────
    section("8. get_account_activities()")
    try:
        activities = broker.get_account_activities(["OPASN", "OPEXP", "OPEXC", "OPTRD"])
        logger.info("Activities returned: %d", len(activities))
        for a in activities[:5]:
            logger.info("  %s", a)
    except Exception:
        logger.exception("get_account_activities FAILED")

    # ── done ─────────────────────────────────────────────────
    section("Smoke test complete")


if __name__ == "__main__":
    main()
