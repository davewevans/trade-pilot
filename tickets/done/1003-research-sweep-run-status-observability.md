# Research sweep run-status observability (`1003-research-sweep-run-status-observability`)

**Status:** done
**Synthesized:** 2026-07-03

## Purpose

The Research Dashboard showed "Last sweep run: never" even when the weekly sweep was actually
firing every Sunday and aborting in pre-flight — the ORATS-budget abort calls `sys.exit(3)`
before the code that wrote `research_last_run.json`, so an operator couldn't distinguish "never
scheduled" from "aborts every week." This ticket records a run-status (including abort reason) on
every exit path and surfaces it through `/api/research/last-run` and the dashboard.
Observability only — it does not change budget policy or let the sweep spend more.

## What was done

- `jobs/weekly_research.py` — added a shared `_write_run_status(status, *, reason, detail,
  scan_stats)` helper that atomically (best-effort, never raises) writes
  `research_last_run.json`. Both `sys.exit(3)` abort paths in `_run_preflight_check` now call it
  with `status="aborted", reason="orats_budget"` before exiting; exit codes/thresholds/notify
  unchanged. The success path in `run()` now goes through the same writer with `status="ok"`.
- `api/server.py` — `research_last_run()` now passes through `status`, `abort_reason`, `detail`
  from the file into the response (default `None`); DB recency queries untouched.
- `frontend/src/api/client.ts` — extended the `ResearchLastRunResponse` type with the three new
  optional fields.
- `frontend/src/pages/Research.tsx` — added a `LastSweepBadge` in the Research Dashboard header
  (next to the existing scan `StalenessBadge`) that renders "Last sweep: aborted — ORATS budget"
  (with detail as tooltip) on `status === "aborted"`, relative timestamp on `"ok"`, else "never".
  Note: `CoverageStats.tsx`'s separate "Last Sweep Run" text is fed by a different endpoint
  (`/api/research/winrate/coverage`, backed by `backtest_sweep_state.json`) and was correctly
  left untouched.
- `tests/test_weekly_research_preflight.py` — added `test_preflight_abort_writes_run_status`,
  driving the hard-budget-overflow abort branch and asserting the status file is written.

## Key files

- `jobs/weekly_research.py` — `_write_run_status`, `_run_preflight_check`, `run()`.
- `api/server.py:~3034` — `research_last_run()`.
- `frontend/src/api/client.ts`, `frontend/src/pages/Research.tsx` — new `status` field + badge.
- `tests/test_weekly_research_preflight.py` — abort-path regression test.

## Notes for future work

Gate: `pytest tests/test_weekly_research_preflight.py tests/test_api_research_winrate.py -q`
showed 9 pre-existing failures in `test_api_research_winrate.py` (confirmed identical, same test
names, on `development` *before* this ticket's changes — unrelated `winrate/*` endpoint grouping
bug, not introduced here). The ticket's actual target, `test_weekly_research_preflight.py`, passes
6/6 in isolation. `cd frontend && npm run build` also passes clean. Per the ticket's own
"Post-merge (user)" note: confirm on the dashboard that the Research card now reads "aborted —
ORATS budget" instead of "never" — the actual budget-policy fix (raise it, run as a subprocess so
`sys.exit` can't touch the scheduler, or chunk the sweep) is a separate human-authored ticket. Also
worth checking whether `weekly_research` runs in-process or as its own subprocess — a `sys.exit(3)`
inside an in-process scheduled job could take down the scheduler loop.
