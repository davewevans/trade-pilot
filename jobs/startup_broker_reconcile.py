"""Startup broker reconcile — runs once on scheduler boot.

Fetches positions/orders/cash/buying_power for every account from Alpaca,
rebuilds strategy state from broker data, diffs against SQLite/JSON local
state. On mismatch in enforce mode: overwrite local state. Above halt
threshold: write HALTED.lock.

IMPORTANT: This reconciler runs AFTER jobs.startup_reconciler (pending-order
reconciler). It must not modify the trades table under any circumstances.
"""

import json
import logging
from dataclasses import asdict

from database.db import Database
from database.repositories.strategy_states import StrategyStateRepository
from data.spread_tracker import SpreadTracker
from database.recorder import TradeRecorder

logger = logging.getLogger(__name__)


def run() -> dict:
    """Run the startup broker reconciliation.

    Returns:
        dict with keys: enabled, mode, accounts_checked, accounts_failed,
        position_diffs, cash_diffs, order_diffs, severity, halted, report_path.
    """
    from config import settings

    summary: dict = {
        "enabled": settings.STARTUP_RECONCILE_ENABLED,
        "mode": settings.DROP_COPY_ENFORCEMENT_MODE,
        "accounts_checked": 0,
        "accounts_failed": [],
        "position_diffs": 0,
        "cash_diffs": 0,
        "order_diffs": 0,
        "severity": "none",
        "halted": False,
        "report_path": str(settings.SNAPSHOTS_DIR / "startup_reconcile_report.json"),
    }

    if not settings.STARTUP_RECONCILE_ENABLED:
        logger.info("startup_broker_reconcile: disabled (STARTUP_RECONCILE_ENABLED=false)")
        return summary

    mode = settings.DROP_COPY_ENFORCEMENT_MODE

    # ── Fetch broker state ────────────────────────────────────────────────
    from jobs._broker_snapshot import fetch_all_accounts
    snapshots = fetch_all_accounts()

    # ── Load local state ──────────────────────────────────────────────────
    strategy_states: list[dict] = []
    try:
        _db = Database()
        _db.init_schema()
        _conn = _db.get_connection()
        strategy_states = StrategyStateRepository(_conn).get_all()
    except Exception:
        logger.exception("startup_broker_reconcile: failed to load strategy_states from SQLite")

    tracker_open_spreads: list[dict] = []
    try:
        tracker_open_spreads = SpreadTracker().get_active_spreads()
    except Exception:
        logger.exception("startup_broker_reconcile: failed to load SpreadTracker")

    local_pending_order_ids: set[str] = _collect_pending_order_ids(tracker_open_spreads)

    prior_cash_by_account = _load_prior_cash(snapshots, settings)

    # ── Build diff ────────────────────────────────────────────────────────
    from jobs._reconcile_diff import build_diff
    diff = build_diff(
        snapshots=snapshots,
        tracker_open_spreads=tracker_open_spreads,
        strategy_states=strategy_states,
        local_pending_order_ids=local_pending_order_ids,
        prior_cash_by_account=prior_cash_by_account,
        settings_obj=settings,
    )

    # ── Write report (always, before any enforcement) ─────────────────────
    report_path = settings.SNAPSHOTS_DIR / "startup_reconcile_report.json"
    _write_report(report_path, diff, snapshots)
    summary["report_path"] = str(report_path)

    # ── Populate summary ──────────────────────────────────────────────────
    summary["accounts_checked"] = diff.accounts_fetched
    summary["accounts_failed"] = diff.accounts_failed
    summary["position_diffs"] = len(diff.position_diffs)
    summary["cash_diffs"] = len(diff.cash_diffs)
    summary["order_diffs"] = len(diff.order_diffs)
    summary["severity"] = diff.severity

    logger.info(
        "startup_broker_reconcile: accounts_checked=%d failed=%s "
        "position_diffs=%d cash_diffs=%d order_diffs=%d severity=%s mode=%s",
        diff.accounts_fetched, diff.accounts_failed,
        len(diff.position_diffs), len(diff.cash_diffs), len(diff.order_diffs),
        diff.severity, mode,
    )
    if diff.severity_reasons:
        for reason in diff.severity_reasons:
            logger.warning("startup_broker_reconcile: %s", reason)

    # ── Notifications ─────────────────────────────────────────────────────
    _notify_if_needed(diff, mode, report_path)

    # ── Log-only mode: report only, no mutations ──────────────────────────
    if mode == "log_only":
        logger.info(
            "startup_broker_reconcile: log_only mode — no state mutations. "
            "Report: %s", report_path,
        )
        return summary

    # ── Enforce mode ──────────────────────────────────────────────────────
    if mode == "enforce":
        _apply_enforcement(diff, tracker_open_spreads, strategy_states, settings)

        if (
            diff.severity == "red"
            or diff.total_abs_position_delta_usd > settings.STARTUP_RECONCILE_HALT_THRESHOLD_USD
        ):
            reason = (
                f"startup_reconcile_mismatch: severity={diff.severity}, "
                f"untracked_delta=${diff.total_abs_position_delta_usd:.2f}"
            )
            _write_halt_lock(reason)
            summary["halted"] = True
            logger.critical(
                "startup_broker_reconcile: HALTED — %s", reason,
            )

    return summary


# ── helpers ───────────────────────────────────────────────────────────────────

def _collect_pending_order_ids(tracker_open_spreads: list[dict]) -> set[str]:
    """Build the set of locally-tracked pending order IDs."""
    ids: set[str] = set()
    for spread in tracker_open_spreads:
        for key in ("entry_order_id", "close_order_id", "recovery_close_order_id"):
            oid = spread.get(key)
            if oid:
                ids.add(str(oid))
    # Also collect SQLite pending trades
    try:
        _db = Database()
        _db.init_schema()
        _conn = _db.get_connection()
        _rec = TradeRecorder(_conn)
        for row in _rec.trades.get_pending():
            oid = row.get("alpaca_order_id")
            if oid:
                ids.add(str(oid))
    except Exception:
        logger.debug("startup_broker_reconcile: could not load DB pending order IDs")
    return ids


def _load_prior_cash(snapshots, settings) -> dict[str, float | None]:
    """Load prior cash values from per-account snapshot files."""
    result: dict[str, float | None] = {}
    for snap in snapshots:
        try:
            snap_file = settings.SNAPSHOTS_DIR / f"{snap.account_name}.json"
            if snap_file.exists():
                data = json.loads(snap_file.read_text(encoding="utf-8"))
                cash_val = data.get("cash") or data.get("account", {}).get("cash")
                if cash_val is not None:
                    result[snap.account_name] = float(cash_val)
                else:
                    result[snap.account_name] = None
            else:
                result[snap.account_name] = None
        except Exception:
            result[snap.account_name] = None
    return result


def _write_report(report_path, diff, snapshots) -> None:
    """Write the full reconcile diff to a JSON report file."""
    try:
        from dataclasses import asdict
        from utils.fileio import atomic_json_write
        report_data = {
            **asdict(diff),
            "snapshots_summary": [
                {
                    "account_name": s.account_name,
                    "strategy_keys": s.strategy_keys,
                    "fetched_ok": s.fetched_ok,
                    "error": s.error,
                    "portfolio_value": s.portfolio_value,
                    "cash": s.cash,
                    "buying_power": s.buying_power,
                    "position_count": len(s.positions),
                    "open_order_count": len(s.open_orders),
                }
                for s in snapshots
            ],
        }
        atomic_json_write(report_path, report_data)
        logger.debug("startup_broker_reconcile: report written to %s", report_path)
    except Exception:
        logger.exception("startup_broker_reconcile: failed to write report to %s", report_path)


def _notify_if_needed(diff, mode: str, report_path) -> None:
    """Fire ntfy notification based on severity and enforcement mode."""
    try:
        from notifications import notify
    except Exception:
        return

    if diff.severity == "none":
        return

    if mode == "log_only":
        if diff.severity in ("yellow", "red"):
            try:
                notify(
                    "warning",
                    "Startup reconcile: mismatch detected (log-only)",
                    f"Severity: {diff.severity}. Reasons: {'; '.join(diff.severity_reasons[:3])}. "
                    f"Report: {report_path}",
                    tags=["reconcile", "startup"],
                )
            except Exception:
                logger.debug("startup_broker_reconcile: ntfy failed", exc_info=True)
    elif mode == "enforce":
        level = "critical" if diff.severity == "red" else "high"
        try:
            notify(
                level,
                f"Startup reconcile: {diff.severity.upper()} mismatch",
                f"Reasons: {'; '.join(diff.severity_reasons[:3])}. Report: {report_path}",
                tags=["reconcile", "startup", diff.severity],
            )
        except Exception:
            logger.debug("startup_broker_reconcile: ntfy failed", exc_info=True)


def _apply_enforcement(diff, tracker_open_spreads: list[dict],
                       strategy_states: list[dict], settings) -> None:
    """Overwrite local state to match broker truth for qty/untracked mismatches."""
    from jobs._reconcile_diff import PositionDiff
    from jobs._reconcile_logic import derive_wheel_state_from_positions
    from jobs._broker_snapshot import fetch_all_accounts

    # Reload snapshots for position data (already fetched but re-using diff)
    # We only need positions for mismatch overwrite.
    snapshots_by_account = {
        s.account_name: s
        for s in fetch_all_accounts()
        if s.fetched_ok
    }

    for pd in diff.position_diffs:
        if pd.category not in ("qty_mismatch", "untracked"):
            continue

        snap = snapshots_by_account.get(pd.account_name)
        if snap is None:
            continue

        from utils.occ import parse_occ, extract_root
        parsed = parse_occ(pd.symbol)

        if parsed is not None:
            # Option position — update wheel strategy_states
            root = parsed["root"]
            _update_wheel_state_from_broker(
                underlying=root,
                positions=snap.positions,
                strategy_states=strategy_states,
                settings=settings,
            )
        else:
            # Equity position
            _update_wheel_state_from_broker(
                underlying=pd.symbol,
                positions=snap.positions,
                strategy_states=strategy_states,
                settings=settings,
            )


def _update_wheel_state_from_broker(
    underlying: str,
    positions: list[dict],
    strategy_states: list[dict],
    settings,
) -> None:
    """Overwrite wheel / turnover_wheel strategy_states row to match broker positions."""
    from jobs._reconcile_logic import derive_wheel_state_from_positions

    # Determine which strategy types track this underlying
    for stype in ("wheel", "turnover_wheel"):
        row = next(
            (r for r in strategy_states
             if r.get("strategy_type") == stype
             and (r.get("underlying") or "").upper() == underlying.upper()),
            None,
        )
        if row is None:
            continue

        broker_state = derive_wheel_state_from_positions(underlying, positions)
        local_state = (row.get("state") or "IDLE").upper()

        if broker_state != local_state:
            logger.warning(
                "startup_broker_reconcile: %s/%s broker_state=%s local_state=%s — "
                "overwriting local state",
                stype, underlying, broker_state, local_state,
            )
            try:
                _db = Database()
                _db.init_schema()
                _conn = _db.get_connection()
                repo = StrategyStateRepository(_conn)
                repo.upsert(
                    strategy_type=stype,
                    underlying=underlying,
                    state=broker_state,
                    cycle_id=row.get("active_cycle_id"),
                    payload=row.get("payload"),
                )
                logger.info(
                    "startup_broker_reconcile: updated strategy_states %s/%s → %s",
                    stype, underlying, broker_state,
                )
            except Exception:
                logger.exception(
                    "startup_broker_reconcile: failed to update strategy_states "
                    "for %s/%s", stype, underlying,
                )


def _write_halt_lock(reason: str) -> None:
    """Write HALTED.lock via CircuitBreaker."""
    try:
        from strategies.circuit_breaker import CircuitBreaker
        CircuitBreaker()._write_halt_lock(reason, source="startup_broker_reconcile")
    except Exception:
        logger.exception(
            "startup_broker_reconcile: failed to write HALTED.lock — reason: %s", reason,
        )
