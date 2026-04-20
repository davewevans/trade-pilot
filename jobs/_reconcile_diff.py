"""Broker-truth diff engine for reconciliation jobs.

Takes local state + AccountSnapshot list and emits a structured diff.
Pure data — no side effects, no IO, no broker calls. All inputs come
from the caller (startup_broker_reconcile or drop_copy_reconcile).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from jobs._broker_snapshot import AccountSnapshot


@dataclass
class PositionDiff:
    account_name: str
    symbol: str
    broker_qty: float | None
    local_qty: float | None          # None = not tracked locally
    broker_market_value: float | None
    delta_usd: float                 # abs(broker_value − local_value)
    category: str                    # "untracked" | "qty_mismatch" | "value_drift"


@dataclass
class CashDiff:
    account_name: str
    broker_cash: float
    # "local" cash is derived from prior portfolio snapshot; None if unavailable
    local_cash: float | None
    delta_usd: float | None


@dataclass
class OrderDiff:
    account_name: str
    broker_order_id: str | None
    local_order_id: str | None
    category: str                    # "untracked_broker" | "orphan_local" | "status_mismatch"
    status_broker: str | None
    status_local: str | None


@dataclass
class ReconcileDiff:
    generated_at: str                # ISO8601 UTC
    accounts_fetched: int
    accounts_failed: list[str]
    position_diffs: list[PositionDiff]
    cash_diffs: list[CashDiff]
    order_diffs: list[OrderDiff]
    total_abs_position_delta_usd: float
    severity: str                    # "none" | "yellow" | "red"
    severity_reasons: list[str]      # human-readable strings


def build_diff(
    snapshots: list[AccountSnapshot],
    tracker_open_spreads: list[dict],
    strategy_states: list[dict],
    local_pending_order_ids: set[str],
    prior_cash_by_account: dict[str, float | None],
    settings_obj,
) -> ReconcileDiff:
    """Compute a diff between broker truth and local state.

    Args:
        snapshots: One AccountSnapshot per distinct account.
        tracker_open_spreads: Active spread dicts from SpreadTracker.get_active_spreads().
        strategy_states: All rows from StrategyStateRepository.get_all().
        local_pending_order_ids: Set of order IDs with local pending records.
        prior_cash_by_account: {account_name: prior_cash} from last account snapshot.
        settings_obj: config.settings instance (for threshold constants).
    """
    from utils.occ import parse_occ

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    accounts_failed = [s.account_name for s in snapshots if not s.fetched_ok]

    # Build spread leg index: {occ_symbol: expected_signed_qty}
    spread_leg_index: dict[str, float] = {}
    for spread in tracker_open_spreads:
        for leg in spread.get("legs", []):
            sym = (leg.get("symbol") or "").upper()
            if not sym:
                continue
            intent = str(leg.get("position_intent") or "").lower()
            if "sell_to_open" in intent:
                spread_leg_index[sym] = -1.0
            elif "buy_to_open" in intent:
                spread_leg_index[sym] = 1.0

    # Build wheel-tracked roots index: {root → expected_state}
    # Only non-IDLE rows are "actively tracked" — IDLE means no position expected.
    wheel_tracked: dict[str, str] = {}  # root → state
    for row in strategy_states:
        stype = (row.get("strategy_type") or "").lower()
        state = (row.get("state") or "IDLE").upper()
        underlying = (row.get("underlying") or "").upper()
        if stype in ("wheel", "turnover_wheel") and state != "IDLE" and underlying:
            wheel_tracked[underlying] = state

    position_diffs: list[PositionDiff] = []
    cash_diffs: list[CashDiff] = []
    order_diffs: list[OrderDiff] = []

    for snap in snapshots:
        if not snap.fetched_ok:
            continue

        account_equity = snap.portfolio_value or 0.0

        # ── Position diffs ────────────────────────────────────────────────
        for pos in snap.positions:
            sym = (pos.get("symbol") or "").upper()
            broker_qty = pos.get("qty")
            broker_mv = pos.get("market_value")
            broker_price = pos.get("current_price")
            asset_class = (pos.get("asset_class") or "").lower()
            parsed = parse_occ(sym)

            local_qty: float | None = None
            category: str

            if parsed is not None:
                # Option position
                if sym in spread_leg_index:
                    local_qty = spread_leg_index[sym]
                elif parsed["root"] in wheel_tracked:
                    # Wheel-expected option: -1 for SHORT_PUT or SHORT_CALL
                    wheel_state = wheel_tracked[parsed["root"]]
                    if wheel_state in ("SHORT_PUT", "SHORT_CALL"):
                        local_qty = -1.0
                    else:
                        local_qty = None  # LONG_STOCK doesn't have option positions
                else:
                    local_qty = None  # untracked
            else:
                # Equity / stock position
                if sym in wheel_tracked:
                    wheel_state = wheel_tracked[sym]
                    if wheel_state == "LONG_STOCK":
                        # Expected qty is >= 100; use broker qty as local_qty
                        # if it's ≥ 100 — we're checking existence, not exact qty
                        local_qty = float(broker_qty) if broker_qty is not None else None
                    else:
                        local_qty = None
                else:
                    local_qty = None

            if local_qty is None:
                category = "untracked"
                delta_usd = abs(float(broker_mv)) if broker_mv is not None else 0.0
            elif broker_qty is not None and abs(float(broker_qty) - local_qty) > 0.01:
                category = "qty_mismatch"
                qty_diff = abs(float(broker_qty) - local_qty)
                price = abs(float(broker_price)) if broker_price is not None else 0.0
                # Options: multiply by 100 (contract size)
                multiplier = 100.0 if parsed is not None else 1.0
                delta_usd = qty_diff * price * multiplier
            else:
                # Qty matches — value_drift only
                category = "value_drift"
                delta_usd = 0.0

            # Only record a diff if there's something noteworthy
            if category != "value_drift" or delta_usd > 0:
                position_diffs.append(PositionDiff(
                    account_name=snap.account_name,
                    symbol=sym,
                    broker_qty=float(broker_qty) if broker_qty is not None else None,
                    local_qty=local_qty,
                    broker_market_value=float(broker_mv) if broker_mv is not None else None,
                    delta_usd=round(delta_usd, 2),
                    category=category,
                ))

        # ── Cash diffs ────────────────────────────────────────────────────
        if snap.cash is not None:
            prior_cash = prior_cash_by_account.get(snap.account_name)
            delta = abs(snap.cash - prior_cash) if prior_cash is not None else None
            cash_diffs.append(CashDiff(
                account_name=snap.account_name,
                broker_cash=snap.cash,
                local_cash=prior_cash,
                delta_usd=round(delta, 2) if delta is not None else None,
            ))

        # ── Order diffs ───────────────────────────────────────────────────
        for order in snap.open_orders:
            oid = order.get("id") or ""
            status = order.get("status") or ""
            if oid and oid not in local_pending_order_ids:
                order_diffs.append(OrderDiff(
                    account_name=snap.account_name,
                    broker_order_id=oid,
                    local_order_id=None,
                    category="untracked_broker",
                    status_broker=status,
                    status_local=None,
                ))

    # ── Severity ──────────────────────────────────────────────────────────
    # Compute total untracked position delta (used for halt threshold)
    total_untracked_delta = sum(
        d.delta_usd for d in position_diffs if d.category == "untracked"
    )
    total_abs_position_delta_usd = total_untracked_delta

    severity_reasons: list[str] = []
    severity = "none"

    # RED: filled broker order with no local record
    for od in order_diffs:
        if od.category == "untracked_broker" and (od.status_broker or "") == "filled":
            msg = (
                f"Filled broker order {od.broker_order_id} on {od.account_name} "
                "has no local record"
            )
            severity_reasons.append(msg)
            severity = "red"

    # RED: untracked positions exceed STARTUP_RECONCILE_HALT_THRESHOLD_USD
    halt_threshold = getattr(settings_obj, "STARTUP_RECONCILE_HALT_THRESHOLD_USD", 500.0)
    if total_untracked_delta > halt_threshold:  # strict >
        msg = (
            f"Total untracked position delta ${total_untracked_delta:.2f} exceeds "
            f"halt threshold ${halt_threshold:.2f} (STARTUP_RECONCILE_HALT_THRESHOLD_USD)"
        )
        severity_reasons.append(msg)
        severity = "red"

    # YELLOW: position mismatch above threshold
    pos_mismatch_usd = getattr(settings_obj, "DROP_COPY_POS_MISMATCH_USD", 100.0)
    for pd in position_diffs:
        if pd.category in ("untracked", "qty_mismatch"):
            # Per-account equity for threshold computation
            acct_snap = next(
                (s for s in snapshots if s.account_name == pd.account_name and s.fetched_ok),
                None,
            )
            acct_equity = (acct_snap.portfolio_value or 0.0) if acct_snap else 0.0
            # Threshold: strict > (boundary is not actionable)
            # DROP_COPY_POS_MISMATCH_USD env var; secondary: 1% of account equity
            effective_threshold = min(
                pos_mismatch_usd,
                0.01 * acct_equity if acct_equity > 0 else pos_mismatch_usd,
            )
            if pd.delta_usd > effective_threshold:  # strict >
                msg = (
                    f"{pd.account_name} position {pd.symbol}: "
                    f"delta ${pd.delta_usd:.2f} > threshold ${effective_threshold:.2f} "
                    f"(DROP_COPY_POS_MISMATCH_USD / 1% equity)"
                )
                severity_reasons.append(msg)
                if severity != "red":
                    severity = "yellow"

    # YELLOW: cash mismatch
    cash_mismatch_usd = getattr(settings_obj, "DROP_COPY_CASH_MISMATCH_USD", 100.0)
    for cd in cash_diffs:
        if cd.delta_usd is not None and cd.delta_usd > cash_mismatch_usd:  # strict >
            msg = (
                f"{cd.account_name} cash mismatch: "
                f"broker ${cd.broker_cash:.2f} vs local ${cd.local_cash:.2f} "
                f"(delta ${cd.delta_usd:.2f} > DROP_COPY_CASH_MISMATCH_USD ${cash_mismatch_usd:.2f})"
            )
            severity_reasons.append(msg)
            if severity != "red":
                severity = "yellow"

    return ReconcileDiff(
        generated_at=generated_at,
        accounts_fetched=sum(1 for s in snapshots if s.fetched_ok),
        accounts_failed=accounts_failed,
        position_diffs=position_diffs,
        cash_diffs=cash_diffs,
        order_diffs=order_diffs,
        total_abs_position_delta_usd=round(total_abs_position_delta_usd, 2),
        severity=severity,
        severity_reasons=severity_reasons,
    )
