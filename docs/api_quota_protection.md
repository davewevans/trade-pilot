# ORATS API Quota Protection Runbook

This document describes the nine-phase hardening applied after the 2026-04-17 quota
exhaustion incident (three concurrent `weekly_research` processes consumed the full
20,000-call monthly ORATS budget in ~7 minutes, triggering account suspension).

---

## Architecture Overview

```
┌───────────────────┐  check_and_reserve()  ┌─────────────────────┐
│  orats_historical │ ─────────────────────▶ │   ApiLedger         │
│  orats_client     │                        │  (SQLite WAL)       │
└───────────────────┘  record()             │                     │
                       ─────────────────────▶ │  caps: monthly/     │
                                             │  daily/minute       │
                                             └─────────┬───────────┘
                                                       │
                              ┌────────────────────────┼──────────────────┐
                              ▼                        ▼                  ▼
                    api_calls.jsonl           api_usage.json         ntfy alerts
                    (structured log)          (snapshot)             (threshold)
```

---

## Phase 1 — Process Singleton Lock

**File:** `utils/process_lock.py`

Prevents concurrent instances of the same job from running simultaneously.
`main.py` acquires a PID lock before dispatching any job. If the lock is held
by a live Python process, the new process exits with code 2.

```
python main.py --job weekly_research    # acquires data/locks/job-weekly_research.lock
```

**Lock files:** `DATA_DIR/locks/job-<name>.lock`, `DATA_DIR/locks/scheduler.lock`

Stale locks (process no longer alive) are automatically removed.

---

## Phase 2 — API Usage Ledger & Hard Caps

**File:** `data/api_ledger.py`

Every outbound ORATS call is recorded in `api_usage_ledger` (SQLite). Before each
HTTP request, `check_and_reserve()` enforces three rolling-window caps:

| Cap          | Default       | Env var                            |
|--------------|---------------|------------------------------------|
| Monthly      | 14,000        | `ORATS_HISTORICAL_MONTHLY_CAP`     |
| Daily (24h)  | 14,000        | `ORATS_HISTORICAL_DAILY_CAP`       |
| Per-minute   | 600           | `ORATS_HISTORICAL_MINUTE_CAP`      |
| Live monthly | 4,000         | `ORATS_LIVE_MONTHLY_CAP`           |
| Live daily   | 700           | `ORATS_LIVE_DAILY_CAP`             |
| Live/minute  | 120           | `ORATS_LIVE_MINUTE_CAP`            |

When a cap is exceeded, `OratsQuotaExceeded` is raised, a blocked row is written
to the ledger, and the call is skipped cleanly.

The day window is **rolling 24 hours** (not a calendar day) to handle overnight
sweeps that cross midnight.

---

## Phase 3 — Pre-flight Budget Check

**File:** `jobs/weekly_research.py` → `_run_preflight_check()`

Before the backtest sweep begins, the estimated ORATS call count is compared to
the remaining monthly budget:

- `warm_estimate > month_remaining` → `sys.exit(3)` + ntfy critical alert
- `warm_estimate > 0.8 × month_remaining` (without override) → `sys.exit(3)`
- Set `ORATS_ALLOW_BUDGET_HEAVY=1` to proceed when estimate > 80% (e.g. after
  a large manual query run)

---

## Phase 4 — Structured API Call Log

**File:** `utils/json_log_formatter.py`, wired in `main.py`

Every API call (success, cache hit, or blocked) appends one JSON line to
`LOG_DIR/api_calls.jsonl`.  This logger has `propagate=False` so lines never
bleed into `trade-pilot.log`.

```bash
# Inspect today's live calls
jq 'select(.api == "orats_historical" and .cache_hit == false)' data/logs/api_calls.jsonl

# Count calls by endpoint
jq -r '.endpoint' data/logs/api_calls.jsonl | sort | uniq -c
```

---

## Phase 5 — Usage Snapshot & API Endpoint

**File:** `data/api_ledger.py` → `write_snapshot()`, `api/server.py` → `GET /api/usage`

`api_usage.json` is written to `SNAPSHOTS_DIR` automatically:
- Every 50 billable calls
- When a quota cap is exceeded
- On process exit (atexit hook)

The dashboard can poll `GET /api/usage` to monitor live ORATS budget without
querying SQLite directly. Auth-gated by the existing middleware.

---

## Phase 6 — ntfy Threshold Alerts

**File:** `data/api_ledger.py` → `_check_monthly_thresholds()`, `_notify_quota_exceeded()`

Automatic ntfy critical alerts fire when:

| Condition                         | Behaviour                        |
|-----------------------------------|----------------------------------|
| Monthly usage crosses 50%         | One alert per threshold per month |
| Monthly usage crosses 75%         | One alert per threshold per month |
| Monthly usage crosses 90%         | One alert per threshold per month |
| Monthly usage crosses 95%         | One alert per threshold per month |
| `OratsQuotaExceeded` raised       | Immediate alert (minute_cap rate-limited to 1/5 min) |
| Pre-flight budget check aborts    | Immediate ntfy critical alert    |

---

## Phase 7 — Kill Switch

**File:** `data/api_ledger.py`, `api/server.py`

An admin can instantly disable all ORATS calls by writing
`DATA_DIR/ORATS_DISABLED.lock`. When present, every `check_and_reserve()` call
raises `OratsDisabled` before any cap check.

### Admin endpoints (auth-gated)

```bash
# Disable ORATS immediately
curl -X POST https://your-api/api/admin/orats/disable \
  -H "Authorization: Bearer <token>" \
  -d '{"reason": "quota emergency"}'

# Re-enable
curl -X POST https://your-api/api/admin/orats/enable \
  -H "Authorization: Bearer <token>"

# Check status
curl https://your-api/api/admin/orats/status \
  -H "Authorization: Bearer <token>"
```

### Manual (SSH / file system)

```bash
# Disable
echo "quota emergency" > data/ORATS_DISABLED.lock

# Re-enable
rm data/ORATS_DISABLED.lock
```

---

## Phase 8 — Sweep Resume Safety

**File:** `research/backtesting/sweep.py`, `jobs/weekly_research.py`, `main.py`

If `backtest_sweep_state.json` contains an in-progress run (no `last_completed_at`)
and the file was last modified >1 hour ago, the run is assumed to have crashed.
The state is automatically reset with a WARNING log rather than blindly resuming,
which could cause API calls for already-processed symbols.

To force-restart manually:

```bash
python main.py --job weekly_research --force-restart-sweep
# or
FORCE_RESTART_SWEEP=1 python main.py --job weekly_research
```

---

## Environment Variables Reference

| Variable                          | Default  | Purpose                                      |
|-----------------------------------|----------|----------------------------------------------|
| `ORATS_HISTORICAL_MONTHLY_CAP`    | 14000    | Hard monthly cap for orats_historical calls  |
| `ORATS_HISTORICAL_DAILY_CAP`      | 14000    | Hard rolling-24h cap                         |
| `ORATS_HISTORICAL_MINUTE_CAP`     | 600      | Hard rolling-60s cap                         |
| `ORATS_LIVE_MONTHLY_CAP`          | 4000     | Hard monthly cap for orats_live calls        |
| `ORATS_LIVE_DAILY_CAP`            | 700      | Hard rolling-24h cap for live calls          |
| `ORATS_LIVE_MINUTE_CAP`           | 120      | Hard rolling-60s cap for live calls          |
| `ORATS_ALLOW_BUDGET_HEAVY`        | 0        | Set to 1 to bypass the 80% pre-flight gate   |
| `FORCE_RESTART_SWEEP`             | 0        | Set to 1 to discard stale sweep state        |

---

## Monitoring Queries

```bash
# Current usage summary
curl https://your-api/api/usage -H "Authorization: Bearer <token>"

# Recent blocked calls
jq 'select(.blocked_reason != null)' data/logs/api_calls.jsonl | tail -20

# Calls per minute in last hour
jq -r '.ts' data/logs/api_calls.jsonl \
  | grep "$(date -u +%Y-%m-%dT%H)" \
  | cut -c1-16 | sort | uniq -c
```
