"""Startup order reconciler — runs once at bot startup.

PURPOSE
-------
This module reconciles pending local state (spread_tracker PENDING_* entries
and SQLite trades with fill_status='pending') against the live Alpaca order
status for each trading account. It runs at startup to recover from:
  - Bot crashes mid-trade
  - Render restarts between order submission and fill confirmation
  - Deployments that interrupt the market_close reconciler

HALT-GATE POLICY
----------------
HALTED.lock does NOT gate this reconciler — state truth is always needed
regardless of halt status. Knowing what actually happened to pending orders
is prerequisite to any safe resumption of trading, and the CB halt gate only
applies to NEW trading decisions, not to reading order status from Alpaca.
No new orders are placed here.

HARD CONSTRAINT
---------------
This file has NO execute_decision(), place_order(), or order-creation call
path — state corrections only. All Alpaca calls are read-only (get_orders,
get_order). Local state is updated via spread_tracker and DB updates only.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Terminal Alpaca order statuses
_TERMINAL_FILLED = frozenset({"filled"})
_TERMINAL_CANCELED = frozenset({"canceled", "cancelled", "rejected", "expired"})
_TERMINAL_ALL = _TERMINAL_FILLED | _TERMINAL_CANCELED


def run() -> dict:
    """Reconcile pending local state against live Alpaca order status.

    Returns summary dict: {
        "accounts_checked": int,
        "orders_reconciled": int,
        "orphan_orders_on_alpaca": int,
        "orphan_pending_local": int,
        "account_failures": list[str]
    }
    """
    from brokers.broker_factory import make_broker
    from data.spread_tracker import SpreadTracker, STATUS_PENDING_OPEN, STATUS_PENDING_CLOSE
    from strategies.circuit_breaker import PORTFOLIO_ACCOUNTS

    summary: dict = {
        "accounts_checked": 0,
        "orders_reconciled": 0,
        "orphan_orders_on_alpaca": 0,
        "orphan_pending_local": 0,
        "account_failures": [],
    }

    # Mapping mirrors startup_snapshot.ACCOUNT_BROKER_MAP
    _ACCOUNT_TO_STRATEGY = {
        "wheel": "wheel",
        "iron_condor": "iron_condor",
        "spreads": "bull_put_spread",
    }

    # Load spread tracker (shared across all accounts — it's a single JSON file)
    try:
        tracker = SpreadTracker()
    except Exception:
        logger.exception("startup_reconciler: failed to load SpreadTracker — aborting")
        return summary

    # Load local PENDING trades from SQLite
    local_pending_db: list[dict] = []
    _recorder = None
    try:
        from database.db import Database
        from database.recorder import TradeRecorder
        _db = Database()
        _db.init_schema()
        _conn = _db.get_connection()
        _recorder = TradeRecorder(_conn)
        local_pending_db = _recorder.trades.get_pending()
    except Exception:
        logger.exception("startup_reconciler: failed to load pending DB trades")

    # Index local pending DB trades by alpaca_order_id for O(1) lookup
    db_pending_by_order_id: dict[str, dict] = {
        row["alpaca_order_id"]: row
        for row in local_pending_db
        if row.get("alpaca_order_id")
    }

    # Index spread tracker pending spreads by entry_order_id or close_order_id
    spread_pending = tracker.get_active_spreads()  # PENDING_OPEN + OPEN + PENDING_CLOSE
    spread_pending_by_order_id: dict[str, dict] = {}
    for spread in spread_pending:
        status = spread.get("status", "")
        if status == STATUS_PENDING_OPEN:
            oid = spread.get("entry_order_id")
            if oid:
                spread_pending_by_order_id[oid] = spread
        elif status == STATUS_PENDING_CLOSE:
            oid = spread.get("close_order_id")
            if oid:
                spread_pending_by_order_id[oid] = spread

    # Track which order IDs we've seen from Alpaca (for orphan detection)
    seen_alpaca_order_ids: set[str] = set()

    # ── Per-account reconciliation ──────────────────────────
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    for acct_name in PORTFOLIO_ACCOUNTS:
        strategy_key = _ACCOUNT_TO_STRATEGY[acct_name]
        try:
            broker = make_broker(strategy_key)
        except Exception as exc:
            logger.error(
                "startup_reconciler: failed to create broker for %s: %s", acct_name, exc
            )
            summary["account_failures"].append(acct_name)
            continue

        summary["accounts_checked"] += 1

        # Fetch open/working orders
        alpaca_orders: list[dict] = []
        try:
            open_orders = broker.get_orders(status="open", limit=100)
            alpaca_orders.extend(open_orders)
        except Exception:
            logger.warning(
                "startup_reconciler: get_orders(open) failed for %s", acct_name, exc_info=True
            )

        # Fetch recently-closed (filled/canceled/etc.) orders from the last 24 hours
        try:
            closed_orders = broker.get_orders(status="closed", limit=100)
            # Filter to last 24 hours by submitted_at or filled_at
            for o in closed_orders:
                submitted = o.get("submitted_at")
                filled = o.get("filled_at")
                ts_str = filled or submitted or ""
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                        if ts >= cutoff:
                            alpaca_orders.append(o)
                    except (ValueError, TypeError):
                        # Can't parse timestamp — include it to be safe
                        alpaca_orders.append(o)
                else:
                    alpaca_orders.append(o)
        except Exception:
            logger.warning(
                "startup_reconciler: get_orders(closed) failed for %s", acct_name, exc_info=True
            )

        # Deduplicate by order id
        seen_ids: set[str] = set()
        deduped: list[dict] = []
        for o in alpaca_orders:
            oid = str(o.get("id") or "")
            if oid and oid not in seen_ids:
                seen_ids.add(oid)
                deduped.append(o)
        alpaca_orders = deduped

        for order in alpaca_orders:
            order_id = str(order.get("id") or "")
            if not order_id:
                continue
            seen_alpaca_order_ids.add(order_id)

            alpaca_status = str(order.get("status") or "").lower()
            is_terminal_fill = alpaca_status in _TERMINAL_FILLED
            is_terminal_cancel = alpaca_status in _TERMINAL_CANCELED
            is_terminal = is_terminal_fill or is_terminal_cancel

            # ── Reconcile spread tracker ────────────────────
            if order_id in spread_pending_by_order_id:
                spread = spread_pending_by_order_id[order_id]
                spread_id = spread.get("spread_id")
                local_status = spread.get("status", "")

                if is_terminal:
                    if local_status == STATUS_PENDING_OPEN:
                        if is_terminal_fill:
                            try:
                                tracker.mark_open(spread_id)
                                logger.info(
                                    "startup_reconciler: spread %s PENDING_OPEN → OPEN "
                                    "(order %s filled)", spread_id, order_id
                                )
                                summary["orders_reconciled"] += 1
                            except Exception:
                                logger.exception(
                                    "startup_reconciler: failed to mark_open spread %s", spread_id
                                )
                        else:
                            # canceled/rejected/expired → CANCELED (idle/reverted)
                            try:
                                tracker.cancel_pending_open(
                                    spread_id,
                                    reason=f"order {order_id} was {alpaca_status}",
                                )
                                logger.info(
                                    "startup_reconciler: spread %s PENDING_OPEN → CANCELED "
                                    "(order %s %s)", spread_id, order_id, alpaca_status
                                )
                                summary["orders_reconciled"] += 1
                            except Exception:
                                logger.exception(
                                    "startup_reconciler: failed to cancel_pending_open spread %s",
                                    spread_id,
                                )

                    elif local_status == STATUS_PENDING_CLOSE:
                        if is_terminal_fill:
                            try:
                                tracker.close_spread(spread_id)
                                logger.info(
                                    "startup_reconciler: spread %s PENDING_CLOSE → CLOSED "
                                    "(order %s filled)", spread_id, order_id
                                )
                                summary["orders_reconciled"] += 1
                            except Exception:
                                logger.exception(
                                    "startup_reconciler: failed to close_spread %s", spread_id,
                                )
                        else:
                            # canceled/rejected for a CLOSE — leave for human review
                            logger.warning(
                                "startup_reconciler: spread %s PENDING_CLOSE order %s was %s — "
                                "leaving for human review (manual intervention required)",
                                spread_id, order_id, alpaca_status,
                            )
                # else: working order with local PENDING_* — leave for regular reconciler

            # ── Reconcile DB pending trades ─────────────────
            if order_id in db_pending_by_order_id and _recorder is not None:
                local_trade = db_pending_by_order_id[order_id]
                local_fill_status = str(local_trade.get("fill_status") or "").lower()

                if is_terminal and local_fill_status == "pending":
                    if is_terminal_fill:
                        fill_price_raw = order.get("filled_avg_price")
                        fp = float(fill_price_raw) if fill_price_raw is not None else None
                        _recorder.resolve_trade(order_id, "filled", fill_price=fp)
                        logger.info(
                            "startup_reconciler: DB trade order_id=%s → filled @ %s",
                            order_id, fp,
                        )
                    else:
                        _recorder.resolve_trade(order_id, alpaca_status)
                        logger.info(
                            "startup_reconciler: DB trade order_id=%s → %s",
                            order_id, alpaca_status,
                        )
                    summary["orders_reconciled"] += 1

            # ── Orphan: Alpaca order with no local record ───
            alpaca_has_local = (
                order_id in spread_pending_by_order_id
                or order_id in db_pending_by_order_id
            )
            if not alpaca_has_local:
                logger.warning(
                    "startup_reconciler: orphan Alpaca order id=%s symbol=%s status=%s "
                    "— no local PENDING record found",
                    order_id,
                    order.get("symbol", "?"),
                    alpaca_status,
                )
                summary["orphan_orders_on_alpaca"] += 1

    # ── Orphan local PENDING with no Alpaca match ───────────
    for order_id, spread in spread_pending_by_order_id.items():
        if order_id not in seen_alpaca_order_ids:
            logger.warning(
                "startup_reconciler: orphan local PENDING spread %s (order %s) "
                "— no Alpaca order found. Manual review recommended.",
                spread.get("spread_id"), order_id,
            )
            summary["orphan_pending_local"] += 1

    for order_id, trade in db_pending_by_order_id.items():
        if order_id not in seen_alpaca_order_ids:
            logger.warning(
                "startup_reconciler: orphan local PENDING DB trade id=%s order_id=%s "
                "underlying=%s — no Alpaca order found. Manual review recommended.",
                trade.get("id"), order_id, trade.get("underlying"),
            )
            summary["orphan_pending_local"] += 1

    logger.info(
        "startup_reconciler complete: accounts_checked=%d orders_reconciled=%d "
        "orphan_alpaca=%d orphan_local=%d failures=%s",
        summary["accounts_checked"],
        summary["orders_reconciled"],
        summary["orphan_orders_on_alpaca"],
        summary["orphan_pending_local"],
        summary["account_failures"],
    )
    return summary
