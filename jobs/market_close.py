"""Market close job — runs at 4:00 PM ET every weekday."""

import logging
from datetime import datetime

from config import settings
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Reconcile today's orders, update journal fills, and log EOD summary."""
    logger.info("=== MARKET CLOSE JOB STARTING ===")

    from brokers.broker_factory import get_broker
    from data.trade_journal import TradeJournal

    broker = get_broker()
    journal = TradeJournal(path=settings.JOURNAL_PATH)

    # ── Today's orders ──────────────────────────────────────
    today_str = datetime.now().strftime("%Y-%m-%d")
    orders = broker.get_orders(status="closed")

    filled_count = 0
    cancelled_count = 0
    realized_pnl = 0.0

    for order in orders:
        # Filter to today's orders
        created = str(order.get("created_at", ""))
        if today_str not in created:
            continue

        order_id = order.get("id", "")
        symbol = order.get("symbol", "")
        status = order.get("status", "")

        if status == "filled":
            fill_price = float(order.get("filled_avg_price", 0))
            logger.info("FILLED: %s @ $%.2f", symbol, fill_price)
            journal.update(order_id, {
                "status": "filled",
                "fill_price": fill_price,
            })
            filled_count += 1

            # Accumulate P&L from fill (side-aware)
            side = order.get("side", "")
            qty = int(order.get("filled_qty", order.get("qty", 0)))
            if side == "sell":
                realized_pnl += fill_price * qty * 100  # options premium
            elif side == "buy":
                realized_pnl -= fill_price * qty * 100

        elif status in ("cancelled", "canceled"):
            logger.info("CANCELLED: %s", symbol)
            journal.update(order_id, {"status": "cancelled"})
            cancelled_count += 1

    # ── Portfolio snapshot ──────────────────────────────────
    account = broker.get_account()
    portfolio_value = float(account.get("portfolio_value", 0))
    buying_power = float(account.get("buying_power", 0))
    positions = broker.get_positions()

    # Write current positions snapshot
    snapshot_path = settings.REPORTS_DIR / "positions" / "current.md"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        f.write(f"# Open Positions — {today_str}\n\n")
        f.write(f"Portfolio value: ${portfolio_value:,.2f}\n")
        f.write(f"Buying power: ${buying_power:,.2f}\n\n")
        if positions:
            f.write("| Symbol | Qty | Avg Entry | Market Value | P&L |\n")
            f.write("|--------|-----|-----------|-------------|-----|\n")
            for p in positions:
                f.write(
                    f"| {p.get('symbol', '?')} "
                    f"| {p.get('qty', '?')} "
                    f"| ${float(p.get('avg_entry_price', 0)):,.2f} "
                    f"| ${float(p.get('market_value', 0)):,.2f} "
                    f"| ${float(p.get('unrealized_pl', 0)):,.2f} |\n"
                )
        else:
            f.write("No open positions.\n")

    eod_summary = (
        f"EOD: Portfolio=${portfolio_value:,.2f} | "
        f"Day P&L=${realized_pnl:,.2f} | "
        f"Open Positions={len(positions)} | "
        f"Trades Today={filled_count}"
    )
    logger.info(eod_summary)

    report_body = (
        f"- Portfolio value: ${portfolio_value:,.2f}\n"
        f"- Buying power: ${buying_power:,.2f}\n"
        f"- Realized P&L today: ${realized_pnl:,.2f}\n"
        f"- Filled orders: {filled_count}\n"
        f"- Cancelled orders: {cancelled_count}\n"
        f"- Open positions: {len(positions)}\n"
    )
    append_section("Market Close Summary (4:00 PM ET)", report_body)

    logger.info("=== MARKET CLOSE JOB COMPLETE ===")
