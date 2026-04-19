# Drop-Copy Reconciliation Runbook

Two-phase subsystem that continuously verifies local state matches broker-truth positions on Alpaca.

## Overview

- **Phase 1 — Startup** (`jobs/startup_broker_reconcile.py`): runs once at boot inside `validate_startup()`. Writes `data/startup_reconcile_report.json`. In `enforce` mode, a red severity halts the bot.
- **Phase 2 — Drop-copy** (`jobs/drop_copy_reconcile.py`): runs every 5 minutes during market hours as a scheduled interval job. Writes `data/drop_copy_last_report.json` (rolling overwrite). Applies a grace period before any enforcement action.

## Environment Variables

| Variable | Default | Meaning |
|---|---|---|
| `STARTUP_RECONCILE_ENABLED` | `true` | Run Phase 1 at boot |
| `DROP_COPY_RECONCILE_ENABLED` | `true` | Run Phase 2 every 5 min |
| `DROP_COPY_ENFORCEMENT_MODE` | `log_only` | `log_only` or `enforce` — see below |
| `DROP_COPY_POS_MISMATCH_USD` | `100` | Yellow threshold: absolute position delta in USD |
| `DROP_COPY_POS_MISMATCH_PCT` | `1.0` | Yellow threshold: position delta as % of account equity |
| `STARTUP_RECONCILE_HALT_THRESHOLD_USD` | `500` | Red threshold: total untracked delta in USD |
| `DROP_COPY_CASH_MISMATCH_USD` | `100` | Yellow threshold: cash delta in USD |
| `DROP_COPY_GRACE_CYCLES` | `2` | Number of consecutive 5-min cycles a mismatch must persist before Phase 2 acts |

Thresholds are **strict** — a delta exactly equal to the threshold is "none" severity, not yellow.

## Enforcement Modes

### `log_only` (default)

- Diffs are computed and written to report files on every run.
- **No state mutations** — `wheel_state.json`, `turnover_wheel_state.json`, `open_spreads.json`, and `strategy_states` SQLite table are never modified.
- **No HALTED.lock** is ever written.
- `drop_copy_block.json` is never written.
- When a mismatch first crosses the grace period threshold, a single "warning" notification fires. Subsequent notifications are suppressed for 24 h per key to reduce noise.

### `enforce`

Phase 1 (startup):
- Yellow: notification fired, wheel/turnover-wheel states updated to match broker truth where possible.
- Red (untracked fill detected OR total delta > `STARTUP_RECONCILE_HALT_THRESHOLD_USD`): writes `data/HALTED.lock`, `main.py` exits 1.

Phase 2 (drop-copy), after grace period:
- Yellow: writes `data/drop_copy_block.json` (blocks new entries via `SkipReason.DROP_COPY_BLOCK`), fires "high" notification. Clears block automatically when severity returns to "none".
- Red: writes `data/HALTED.lock`, fires "critical" notification. The bot halts; management cycles do not run.

## Flipping from `log_only` to `enforce`

1. Run in `log_only` for at least one full trading week with zero yellow/red diffs.
2. Review `data/drop_copy_last_report.json` — confirm `"severity": "none"` on recent runs.
3. Set `DROP_COPY_ENFORCEMENT_MODE=enforce` in the environment (Render → Environment → Edit).
4. Redeploy. Phase 1 will run at boot; if clean, the bot starts normally.

Do **not** flip to `enforce` while there are known open mismatches — the bot will halt immediately at startup.

## Where Reports Live

| File | Written by | Contents |
|---|---|---|
| `data/startup_reconcile_report.json` | Phase 1 (every boot) | Full diff: accounts checked, position/cash/order diffs, severity, mode |
| `data/drop_copy_last_report.json` | Phase 2 (rolling overwrite) | Same structure; always reflects the most recent 5-min cycle |
| `data/drop_copy_observations.json` | Phase 2 | Grace-period state: consecutive mismatch counts per key |
| `data/drop_copy_block.json` | Phase 2 (enforce+yellow) | Presence = new entries blocked; absent = clear |

## Reading `startup_reconcile_report.json`

```json
{
  "ts": "2026-04-19T09:30:00+00:00",
  "mode": "log_only",
  "enabled": true,
  "accounts_checked": 6,
  "accounts_failed": 0,
  "severity": "yellow",
  "halted": false,
  "report_path": "data/startup_reconcile_report.json",
  "position_diffs": [
    {
      "account": "wheel",
      "symbol": "AAPL",
      "category": "untracked",
      "broker_qty": -1,
      "local_qty": 0,
      "delta_usd": 187.50,
      "severity": "yellow"
    }
  ],
  "cash_diffs": [],
  "order_diffs": []
}
```

Key fields:
- `severity`: `"none"` | `"yellow"` | `"red"` — worst severity across all accounts
- `halted`: `true` only in `enforce` mode when severity is red
- `accounts_failed`: count of accounts where the Alpaca API call failed (snapshot is incomplete for these)
- `position_diffs[].category`: `"untracked"` (position not in any local state), `"qty_mismatch"` (wrong qty), `"value_drift"` (minor mark-to-market drift, delta=0)
- `order_diffs[].category`: `"untracked_fill"` — a filled order on the broker has no matching local record (always red severity)

## Clearing HALTED.lock After a Reconcile Halt

A reconcile-triggered halt means Phase 1 found a red-severity mismatch at boot. Do not clear the lock until the underlying discrepancy is understood.

1. Read `data/startup_reconcile_report.json` to identify the specific mismatch (symbol, account, delta).
2. Log in to the Alpaca dashboard for the affected account and verify the position manually.
3. If the local state is stale (e.g. a missed fill during a prior crash), update it manually:
   - For wheel/turnover-wheel: edit `data/snapshots/wheel_state.json` or `turnover_wheel_state.json`.
   - For spreads: use `SpreadTracker` tooling or edit `data/snapshots/open_spreads.json` directly.
   - For `strategy_states` SQLite: update via the admin API or `sqlite3` CLI.
4. Once local state matches broker truth, delete `data/HALTED.lock`:
   ```bash
   rm data/HALTED.lock
   ```
5. Restart the bot. Phase 1 will re-run; if clean, the bot starts normally.

## Clearing `drop_copy_block.json` Manually

In `enforce` mode the block is cleared automatically when a drop-copy cycle reports `severity: none`. If you need to clear it manually (e.g. after fixing state out-of-band):

```bash
rm data/drop_copy_block.json
```

The next market_open cycle will treat it as absent and allow new entries.

## Disabling Entirely

Set `DROP_COPY_RECONCILE_ENABLED=false` to skip Phase 2 entirely (Phase 1 respects `STARTUP_RECONCILE_ENABLED`). Both default to `true`. Disabling is not recommended in production — use `log_only` instead to keep visibility without enforcement risk.
