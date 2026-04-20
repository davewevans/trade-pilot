"""Drop-copy reconcile job — runs every 5 minutes during market hours.

Same diff machinery as startup_broker_reconcile but with a grace-period
mechanism and rolling observation log. In enforce mode, persistent
mismatches write a blocker flag that gates new entries in market_open.

Reports:
  data/snapshots/drop_copy_last_report.json  — latest diff (overwritten each run)
  data/snapshots/drop_copy_observations.json — rolling mismatch observation log
  data/snapshots/drop_copy_block.json        — blocker flag for market_open gate
"""

import json
import logging
from datetime import datetime, timezone

from database.db import Database
from database.repositories.strategy_states import StrategyStateRepository
from data.spread_tracker import SpreadTracker
from database.recorder import TradeRecorder

logger = logging.getLogger(__name__)


def run() -> dict:
    """Run the drop-copy reconciliation cycle."""
    from config import settings

    summary: dict = {
        "enabled": settings.DROP_COPY_RECONCILE_ENABLED,
        "mode": settings.DROP_COPY_ENFORCEMENT_MODE,
        "accounts_checked": 0,
        "accounts_failed": [],
        "position_diffs": 0,
        "cash_diffs": 0,
        "order_diffs": 0,
        "severity": "none",
        "report_path": str(settings.SNAPSHOTS_DIR / "drop_copy_last_report.json"),
    }

    if not settings.DROP_COPY_RECONCILE_ENABLED:
        logger.debug("drop_copy_reconcile: disabled (DROP_COPY_RECONCILE_ENABLED=false)")
        return summary

    mode = settings.DROP_COPY_ENFORCEMENT_MODE

    # ── Fetch + diff (mirrors startup_broker_reconcile) ───────────────────
    from jobs._broker_snapshot import fetch_all_accounts
    from jobs.startup_broker_reconcile import (
        _collect_pending_order_ids,
        _load_prior_cash,
        _write_report,
    )

    snapshots = fetch_all_accounts()

    strategy_states: list[dict] = []
    try:
        _db = Database()
        _db.init_schema()
        _conn = _db.get_connection()
        strategy_states = StrategyStateRepository(_conn).get_all()
    except Exception:
        logger.exception("drop_copy_reconcile: failed to load strategy_states")

    tracker_open_spreads: list[dict] = []
    try:
        tracker_open_spreads = SpreadTracker().get_active_spreads()
    except Exception:
        logger.exception("drop_copy_reconcile: failed to load SpreadTracker")

    local_pending_order_ids = _collect_pending_order_ids(tracker_open_spreads)
    prior_cash_by_account = _load_prior_cash(snapshots, settings)

    from jobs._reconcile_diff import build_diff
    diff = build_diff(
        snapshots=snapshots,
        tracker_open_spreads=tracker_open_spreads,
        strategy_states=strategy_states,
        local_pending_order_ids=local_pending_order_ids,
        prior_cash_by_account=prior_cash_by_account,
        settings_obj=settings,
    )

    # ── Write rolling report (always, before enforcement) ─────────────────
    report_path = settings.SNAPSHOTS_DIR / "drop_copy_last_report.json"
    _write_report(report_path, diff, snapshots)
    summary["report_path"] = str(report_path)

    # Populate summary
    summary["accounts_checked"] = diff.accounts_fetched
    summary["accounts_failed"] = diff.accounts_failed
    summary["position_diffs"] = len(diff.position_diffs)
    summary["cash_diffs"] = len(diff.cash_diffs)
    summary["order_diffs"] = len(diff.order_diffs)
    summary["severity"] = diff.severity

    logger.debug(
        "drop_copy_reconcile: accounts=%d failed=%s pos_diffs=%d cash_diffs=%d "
        "order_diffs=%d severity=%s",
        diff.accounts_fetched, diff.accounts_failed,
        len(diff.position_diffs), len(diff.cash_diffs), len(diff.order_diffs),
        diff.severity,
    )

    # ── Grace-period observation tracking ────────────────────────────────
    obs_path = settings.SNAPSHOTS_DIR / "drop_copy_observations.json"
    observations = _load_observations(obs_path)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Build the set of mismatch keys seen this cycle
    current_keys: set[str] = set()
    for pd in diff.position_diffs:
        if pd.category in ("untracked", "qty_mismatch"):
            key = f"{pd.account_name}::{pd.symbol}::{pd.category}"
            current_keys.add(key)
    for od in diff.order_diffs:
        if od.category == "untracked_broker":
            key = f"{od.account_name}::order::{od.broker_order_id}"
            current_keys.add(key)
    for cd in diff.cash_diffs:
        if cd.delta_usd is not None and cd.delta_usd > getattr(settings, "DROP_COPY_CASH_MISMATCH_USD", 100):
            key = f"{cd.account_name}::cash::delta"
            current_keys.add(key)

    # Update observations: increment present keys, remove absent ones
    for key in current_keys:
        if key in observations:
            observations[key]["last_seen_at"] = now_iso
            observations[key]["consecutive_count"] += 1
        else:
            observations[key] = {
                "first_seen_at": now_iso,
                "last_seen_at": now_iso,
                "consecutive_count": 1,
            }

    keys_to_remove = [k for k in observations if k not in current_keys]
    for k in keys_to_remove:
        del observations[k]

    _save_observations(obs_path, observations, now_iso)

    grace_cycles = getattr(settings, "DROP_COPY_GRACE_CYCLES", 2)

    # Determine which keys are "actionable" (consecutive_count >= grace_cycles)
    actionable_keys = {
        k for k, v in observations.items()
        if v["consecutive_count"] >= grace_cycles
    }

    if not actionable_keys:
        logger.debug(
            "drop_copy_reconcile: %d mismatches observed but none yet actionable "
            "(grace=%d)", len(current_keys), grace_cycles,
        )
        # Clear blocker if all mismatches resolved
        _clear_block_if_clean(diff, settings)
        return summary

    # Mismatches are actionable — decide what to do
    if mode == "log_only":
        _notify_log_only(diff, actionable_keys, observations, settings, report_path)
        return summary

    # ── Enforce mode + actionable mismatches ──────────────────────────────
    if diff.severity == "yellow":
        _apply_enforce_yellow(diff, tracker_open_spreads, strategy_states, settings)
        _write_block_flag(
            settings,
            reason=f"drop_copy_reconcile: yellow mismatch (actionable after {grace_cycles} cycles)",
            affected_accounts=list(diff.accounts_failed) or list({
                pd.account_name for pd in diff.position_diffs
            }),
        )
        _notify_enforce(diff, "high", report_path)

    elif diff.severity == "red":
        reason = (
            "untracked_broker_fill"
            if any(
                od.category == "untracked_broker" and (od.status_broker or "") == "filled"
                for od in diff.order_diffs
            )
            else "untracked_position"
        )
        _write_halt_lock(
            f"drop_copy_reconcile: {reason}; severity=red after {grace_cycles} grace cycles"
        )
        _notify_enforce(diff, "critical", report_path)

    return summary


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_observations(obs_path) -> dict:
    if obs_path.exists():
        try:
            data = json.loads(obs_path.read_text(encoding="utf-8"))
            return data.get("observations", {})
        except Exception:
            logger.debug("drop_copy_reconcile: failed to load observations; starting fresh")
    return {}


def _save_observations(obs_path, observations: dict, updated_at: str) -> None:
    try:
        from utils.fileio import atomic_json_write
        atomic_json_write(obs_path, {
            "updated_at": updated_at,
            "observations": observations,
        })
    except Exception:
        logger.exception("drop_copy_reconcile: failed to save observations")


def _notify_log_only(diff, actionable_keys: set[str], observations: dict,
                     settings, report_path) -> None:
    """Notify once per key when it first crosses the grace threshold, then every 24 h."""
    try:
        from notifications import notify
    except Exception:
        return

    _24H_SECONDS = 86400

    for key in actionable_keys:
        obs = observations[key]
        count = obs["consecutive_count"]
        grace_cycles = getattr(settings, "DROP_COPY_GRACE_CYCLES", 2)

        # Notify on first crossing or if 24 h have elapsed since last notify
        last_notified = obs.get("last_notified_at")
        now_ts = datetime.now(timezone.utc).timestamp()
        should_notify = (count == grace_cycles)  # first crossing
        if last_notified and not should_notify:
            try:
                elapsed = now_ts - datetime.fromisoformat(last_notified).timestamp()
                should_notify = elapsed >= _24H_SECONDS
            except Exception:
                pass

        if should_notify:
            try:
                notify(
                    "warning",
                    "Drop-copy mismatch detected (log-only)",
                    f"Key: {key}. Consecutive cycles: {count}. Report: {report_path}",
                    tags=["reconcile", "drop_copy"],
                )
                obs["last_notified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            except Exception:
                logger.debug("drop_copy_reconcile: ntfy failed for key %s", key, exc_info=True)


def _apply_enforce_yellow(diff, tracker_open_spreads, strategy_states, settings) -> None:
    """Overwrite local state to match broker truth in enforce mode."""
    from jobs.startup_broker_reconcile import _apply_enforcement
    _apply_enforcement(diff, tracker_open_spreads, strategy_states, settings)


def _write_block_flag(settings, reason: str, affected_accounts: list[str]) -> None:
    """Write data/snapshots/drop_copy_block.json to gate new entries."""
    try:
        from utils.fileio import atomic_json_write
        block_path = settings.SNAPSHOTS_DIR / "drop_copy_block.json"
        atomic_json_write(block_path, {
            "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reason": reason,
            "affected_accounts": affected_accounts,
        })
        logger.warning("drop_copy_reconcile: block flag written — %s", reason)
    except Exception:
        logger.exception("drop_copy_reconcile: failed to write block flag")


def _clear_block_if_clean(diff, settings) -> None:
    """Remove the blocker flag if the current cycle shows no actionable mismatches."""
    block_path = settings.SNAPSHOTS_DIR / "drop_copy_block.json"
    if block_path.exists():
        if diff.severity == "none":
            try:
                block_path.unlink()
                logger.info("drop_copy_reconcile: block flag cleared (no mismatches)")
            except Exception:
                logger.warning("drop_copy_reconcile: failed to clear block flag")


def _notify_enforce(diff, level: str, report_path) -> None:
    try:
        from notifications import notify
        notify(
            level,
            f"Drop-copy reconcile: {diff.severity.upper()} mismatch (enforce)",
            f"Reasons: {'; '.join(diff.severity_reasons[:3])}. Report: {report_path}",
            tags=["reconcile", "drop_copy", diff.severity],
        )
    except Exception:
        logger.debug("drop_copy_reconcile: ntfy failed", exc_info=True)


def _write_halt_lock(reason: str) -> None:
    try:
        from strategies.circuit_breaker import CircuitBreaker
        CircuitBreaker()._write_halt_lock(reason, source="drop_copy_reconcile")
    except Exception:
        logger.exception(
            "drop_copy_reconcile: failed to write HALTED.lock — reason: %s", reason,
        )
