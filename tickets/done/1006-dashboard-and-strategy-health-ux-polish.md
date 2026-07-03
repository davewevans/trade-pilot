# Dashboard pending-pill placement + Strategy Health summary strip (`1006-dashboard-and-strategy-health-ux-polish`)

**Status:** done
**Synthesized:** 2026-07-03

## Purpose

Two frontend-only "glance, don't dig" readability fixes from the audit: (1) the "N order(s)
pending fill" pill sat next to the global Accounts header without saying which account owned the
order; (2) the Strategy Health funnel grid is dense, with no quick per-strategy summary above it.
Both are display-only — no backend, API, or data-contract changes.

## What was done

- **Part A (`Dashboard.tsx`):** removed the global pending-fill pill from beside the "Accounts"
  header. `AccountCard` now accepts an optional `pendingCount` prop and renders "N pending
  fill(s)" inline with the card's label when non-zero.
  - **Data-model limitation surfaced during implementation:** the ticket assumed pending-order
    data already carries a per-account id, but it doesn't — the `trades` table has no
    `account_id` column, and `/api/pending-count` doesn't group by `strategy_type` either, so a
    fully correct per-account breakdown isn't derivable from currently-fetched frontend data.
    Rather than fabricate a false split, the implementation uses an honest heuristic: the
    bot-wide pending count is attributed to the one account card only when exactly one account is
    `status === 'active'`; with zero or 2+ active accounts, no card shows a count. A true
    per-strategy breakdown would need a small backend change (e.g. group `/api/pending-count` by
    `strategy_type`, or add an `?account=` filter like `/api/trades` has) — worth a follow-up
    ticket if exact attribution matters.
- **Part B (`StrategyHealth.tsx`):** added a `StrategySummaryStrip` (+ `StrategySummaryTile`)
  between the Fill-Realism panel and the funnel grid, derived from the same week's `rows` data the
  grid already renders (no new fetch, grid/week-selector/fetch untouched). Per active strategy:
  name, total decisions, skip % (`(skip_pre_check + skip_claude) / total`), top skip reason,
  submitted/filled counts. Strategies with zero decisions this week collapse into one
  "N idle strategies" line. Returns `null` gracefully with no current week or no data.

## Key files

- `frontend/src/pages/Dashboard.tsx` — `AccountCard`'s new `pendingCount` prop, single-active-
  account attribution heuristic (~line 1237).
- `frontend/src/pages/StrategyHealth.tsx` — `StrategySummaryStrip` / `StrategySummaryTile`.

## Notes for future work

Gate: `cd frontend && npm run build` (tsc + vite) — clean. Confirmed no diffs under `strategies/`,
`api/`, or `config.py` — genuinely frontend-only. Grep confirms no pending-pill markup remains
beside the "Accounts" `<h2>`. Per the ticket's own note, visual correctness is a post-merge human
check, not a runner gate: confirm the pill lands on the right card and reads sensibly, and that
the Strategy Health strip's numbers match the grid for the current week and idle strategies
collapse as expected. Follow-up candidate: give `/api/pending-count` a `strategy_type`/account
grouping so Part A's heuristic can be replaced with exact per-account attribution.
