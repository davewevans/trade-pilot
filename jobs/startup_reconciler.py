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

EXCEPTION — RECOVERY CLOSE
---------------------------
When a partial fill leaves behind an orphaned long leg with no short hedge
(a "long_orphan" pattern), this module places a single sell-to-close order
to unwind the orphan.  This is the only order-creation path permitted here
and exists solely to protect against unintended directional exposure.  All
other reconciliation actions are read-only state corrections.

HARD CONSTRAINT
---------------
Outside of the ``_attempt_recovery_close`` helper (long_orphan case only),
this file has NO execute_decision(), place_order(), or order-creation call
path — state corrections only. All other Alpaca calls are read-only
(get_orders, get_order, get_option_snapshots).
"""

import logging
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Terminal Alpaca order statuses
_TERMINAL_FILLED = frozenset({"filled"})
_TERMINAL_CANCELED = frozenset({"canceled", "cancelled", "rejected", "expired"})
_TERMINAL_ALL = _TERMINAL_FILLED | _TERMINAL_CANCELED


# ── helpers ──────────────────────────────────────────────────────────


def _write_halted_lock(reason: str) -> None:
    """Write HALTED.lock via CircuitBreaker to gate all future trading."""
    try:
        from strategies.circuit_breaker import CircuitBreaker
        CircuitBreaker()._write_halt_lock(reason)
    except Exception:
        logger.exception(
            "startup_reconciler: failed to write HALTED.lock — reason: %s", reason
        )


def _classify_partial_fill(order: dict) -> tuple[str, list[dict], list[dict]]:
    """Classify a partially-filled multi-leg order.

    Inspects each leg's ``position_intent`` and ``status`` fields to determine
    which legs filled and which didn't.

    Returns ``(kind, short_filled_legs, long_filled_legs)`` where *kind* is:
      - ``"none"``        — no recognisable dangerous pattern
      - ``"naked_short"`` — short leg(s) filled, long leg(s) unfilled → HALT
      - ``"long_orphan"`` — long leg(s) filled, short leg(s) unfilled → recovery close
    """
    legs = order.get("legs") or []
    if not legs:
        return "none", [], []

    short_filled: list[dict] = []
    short_unfilled: list[dict] = []
    long_filled: list[dict] = []
    long_unfilled: list[dict] = []

    for leg in legs:
        intent_str = str(leg.get("position_intent") or "").lower()
        status = str(leg.get("status") or "").lower()
        is_filled = status == "filled"
        if "sell_to_open" in intent_str:
            (short_filled if is_filled else short_unfilled).append(leg)
        elif "buy_to_open" in intent_str:
            (long_filled if is_filled else long_unfilled).append(leg)

    if short_filled and long_unfilled:
        return "naked_short", short_filled, long_filled
    if long_filled and not short_filled:
        return "long_orphan", [], long_filled
    return "none", [], []


def _attempt_recovery_close(
    broker,
    spread: dict,
    filled_long_legs: list[dict],
    tracker,
) -> bool:
    """Close orphaned long leg(s) at the current midpoint price.

    Places a sell-to-close day-limit order at the snapshot midpoint for each
    filled long leg, then polls broker.get_order() for up to 60 s.

    Tags the spread JSON with ``recovery_close_order_id`` before polling so
    that if this process restarts the existing order is detected on the next
    run instead of being double-submitted.

    Returns True only if every recovery order fills within the timeout.
    """
    symbols = [leg.get("symbol") for leg in filled_long_legs if leg.get("symbol")]
    if not symbols:
        logger.warning("_attempt_recovery_close: no symbols in filled_long_legs")
        return False

    spread_id = spread.get("spread_id")

    # Fetch current midpoints
    try:
        snaps = broker.get_option_snapshots(symbols)
    except Exception:
        logger.exception(
            "_attempt_recovery_close: get_option_snapshots failed for %s", symbols
        )
        return False

    recovery_order_ids: list[str] = []
    for sym in symbols:
        snap = snaps.get(sym) or {}
        mid = snap.get("mid")
        if mid is None:
            logger.warning(
                "_attempt_recovery_close: no midpoint for %s — using $0.01", sym
            )
            mid = 0.01
        limit = max(round(float(mid), 2), 0.01)

        try:
            order = broker.place_order(
                symbol=sym,
                qty=1,
                side="sell",
                order_type="limit",
                time_in_force="day",
                limit_price=limit,
            )
            order_id = str(order.get("id") or order.get("order_id") or order)
            recovery_order_ids.append(order_id)
            logger.info(
                "_attempt_recovery_close: placed sell-to-close %s @ %.2f order=%s",
                sym, limit, order_id,
            )
        except Exception:
            logger.exception(
                "_attempt_recovery_close: failed to place close order for %s", sym
            )
            return False

    if not recovery_order_ids:
        return False

    # Tag spread for idempotency — first order ID is a sufficient sentinel
    try:
        tracker.set_recovery_close_order_id(spread_id, recovery_order_ids[0])
    except Exception:
        logger.warning(
            "_attempt_recovery_close: failed to tag spread %s with recovery_close_order_id",
            spread_id,
        )

    # Poll for up to 60 s
    deadline = time.monotonic() + 60
    pending = set(recovery_order_ids)
    while time.monotonic() < deadline and pending:
        time.sleep(5)
        still_pending: set[str] = set()
        for oid in list(pending):
            try:
                status_order = broker.get_order(oid)
                st = str(status_order.get("status") or "").lower()
                if st == "filled":
                    logger.info("_attempt_recovery_close: order %s filled", oid)
                elif st in _TERMINAL_CANCELED:
                    logger.warning(
                        "_attempt_recovery_close: order %s was %s — fill failed", oid, st
                    )
                else:
                    still_pending.add(oid)
            except Exception:
                logger.warning(
                    "_attempt_recovery_close: get_order failed for %s",
                    oid, exc_info=True,
                )
                still_pending.add(oid)
        pending = still_pending

    if pending:
        logger.warning(
            "_attempt_recovery_close: timed out; orders still pending: %s", pending
        )
        return False

    return True


# ── main entry point ─────────────────────────────────────────────────


def run() -> dict:
    """Reconcile pending local state against live Alpaca order status.

    Returns summary dict: {
        "accounts_checked": int,
        "orders_reconciled": int,
        "orphan_orders_on_alpaca": int,
        "orphan_pending_local": int,
        "account_failures": list[str],
        "halted": bool,
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
        "halted": False,
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

    # Pre-run check: index any in-flight recovery close orders from a prior
    # session so they are handled by the normal per-account scan below.
    recovery_order_index: dict[str, dict] = {}
    for s in tracker.get_active_spreads():
        rc_oid = s.get("recovery_close_order_id")
        if rc_oid:
            recovery_order_index[rc_oid] = s
            logger.info(
                "startup_reconciler: found in-flight recovery close order %s "
                "for spread %s — will reconcile during account scan",
                rc_oid, s.get("spread_id"),
            )

    # Track which order IDs we've seen from Alpaca (for orphan detection)
    seen_alpaca_order_ids: set[str] = set()

    # Track spread partial fills already handled so that the same spread is not
    # processed twice if its order somehow appears in multiple accounts' lists.
    handled_partial_fill_order_ids: set[str] = set()

    # ── Per-account reconciliation ──────────────────────────
    # 7-day lookback catches crashes that span a weekend or Render restart
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    for acct_name in PORTFOLIO_ACCOUNTS:
        if summary["halted"]:
            logger.info(
                "startup_reconciler: halt detected — skipping remaining accounts"
            )
            break

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

        # Fetch open/working orders (includes partially_filled)
        alpaca_orders: list[dict] = []
        try:
            open_orders = broker.get_orders(status="open", limit=100)
            alpaca_orders.extend(open_orders)
        except Exception:
            logger.warning(
                "startup_reconciler: get_orders(open) failed for %s", acct_name, exc_info=True
            )

        # Fetch recently-closed (filled/canceled/etc.) orders from the last 7 days
        try:
            closed_orders = broker.get_orders(status="closed", limit=100)
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

            # ── Reconcile in-flight recovery close orders ─────────────
            if order_id in recovery_order_index:
                recovery_spread = recovery_order_index[order_id]
                rc_spread_id = recovery_spread.get("spread_id")
                if is_terminal_fill:
                    try:
                        tracker.close_spread(rc_spread_id)
                        logger.info(
                            "startup_reconciler: recovery close order %s filled — "
                            "spread %s marked CLOSED", order_id, rc_spread_id,
                        )
                        summary["orders_reconciled"] += 1
                    except Exception:
                        logger.exception(
                            "startup_reconciler: failed to close_spread %s after "
                            "recovery fill", rc_spread_id,
                        )
                elif is_terminal_cancel:
                    logger.warning(
                        "startup_reconciler: recovery close order %s was %s for "
                        "spread %s — re-attempting recovery close",
                        order_id, alpaca_status, rc_spread_id,
                    )
                    rc_legs = recovery_spread.get("legs", [])
                    long_legs = [
                        leg for leg in rc_legs
                        if "buy_to_open" in str(leg.get("position_intent", "")).lower()
                    ]
                    if long_legs:
                        recovered = _attempt_recovery_close(
                            broker, recovery_spread, long_legs, tracker
                        )
                        if not recovered:
                            reason = (
                                f"Spread {rc_spread_id}: recovery close re-attempt "
                                f"failed after prior order {order_id} was {alpaca_status}."
                            )
                            logger.critical("startup_reconciler: HALTING — %s", reason)
                            _write_halted_lock(reason)
                            summary["halted"] = True
                        else:
                            summary["orders_reconciled"] += 1
                    else:
                        logger.warning(
                            "startup_reconciler: no long legs found for recovery "
                            "re-attempt on spread %s", rc_spread_id,
                        )
                # else: still pending — leave for next run
                continue  # skip normal spread/DB reconciliation for this order

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
                                    "startup_reconciler: failed to mark_open spread %s",
                                    spread_id,
                                )
                        else:
                            # canceled/rejected/expired → CANCELED
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
                                    "startup_reconciler: failed to cancel_pending_open "
                                    "spread %s", spread_id,
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
                                    "startup_reconciler: failed to close_spread %s",
                                    spread_id,
                                )
                        else:
                            # canceled/rejected — revert to OPEN so position_check
                            # can manage the still-live position
                            try:
                                tracker.revert_to_open(
                                    spread_id,
                                    reason=f"order {order_id} was {alpaca_status}",
                                )
                                logger.info(
                                    "startup_reconciler: spread %s PENDING_CLOSE → OPEN "
                                    "(close order %s %s)", spread_id, order_id, alpaca_status,
                                )
                                summary["orders_reconciled"] += 1
                            except Exception:
                                logger.exception(
                                    "startup_reconciler: failed to revert_to_open spread %s",
                                    spread_id,
                                )

                elif (
                    alpaca_status == "partially_filled"
                    and local_status == STATUS_PENDING_OPEN
                    and order_id not in handled_partial_fill_order_ids
                ):
                    handled_partial_fill_order_ids.add(order_id)
                    # Classify the partial fill and respond accordingly
                    kind, short_filled, long_filled = _classify_partial_fill(order)
                    if kind == "naked_short":
                        reason = (
                            f"Spread {spread_id}: MLEG order {order_id} partially filled "
                            f"with naked short exposure — {len(short_filled)} short leg(s) "
                            "filled, long hedge(s) unfilled."
                        )
                        logger.critical("startup_reconciler: HALTING — %s", reason)
                        _write_halted_lock(reason)
                        summary["halted"] = True
                    elif kind == "long_orphan":
                        logger.warning(
                            "startup_reconciler: spread %s has long_orphan partial fill "
                            "on order %s — attempting recovery close",
                            spread_id, order_id,
                        )
                        recovered = _attempt_recovery_close(
                            broker, spread, long_filled, tracker
                        )
                        if not recovered:
                            reason = (
                                f"Spread {spread_id}: long_orphan recovery close failed "
                                f"after partial fill on order {order_id}."
                            )
                            logger.critical("startup_reconciler: HALTING — %s", reason)
                            _write_halted_lock(reason)
                            summary["halted"] = True
                        else:
                            summary["orders_reconciled"] += 1
                    else:
                        logger.info(
                            "startup_reconciler: spread %s partially_filled (no dangerous "
                            "pattern) on order %s — leaving for next reconcile cycle",
                            spread_id, order_id,
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

                elif (
                    alpaca_status == "partially_filled"
                    and local_fill_status == "pending"
                ):
                    # A qty=1 single-leg wheel order cannot be legitimately
                    # partially filled — this is an unexpected broker state.
                    reason = (
                        f"Wheel order {order_id} for "
                        f"{local_trade.get('underlying', '?')} is partially_filled "
                        "on a qty=1 contract — broker in unexpected state."
                    )
                    logger.critical("startup_reconciler: HALTING — %s", reason)
                    _write_halted_lock(reason)
                    summary["halted"] = True

            # ── Orphan: Alpaca order with no local record ───
            alpaca_has_local = (
                order_id in spread_pending_by_order_id
                or order_id in db_pending_by_order_id
                or order_id in recovery_order_index
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
        "orphan_alpaca=%d orphan_local=%d halted=%s failures=%s",
        summary["accounts_checked"],
        summary["orders_reconciled"],
        summary["orphan_orders_on_alpaca"],
        summary["orphan_pending_local"],
        summary["halted"],
        summary["account_failures"],
    )
    return summary
