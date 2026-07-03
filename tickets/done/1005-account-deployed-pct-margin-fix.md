# Fix "-295% deployed" and flag stale portfolio Greeks (`1005-account-deployed-pct-margin-fix`)

**Status:** done
**Synthesized:** 2026-07-03

## Purpose

`buying_power_used_pct` computed `(1 - buying_power/equity)*100`, which assumes a cash account;
on paper_1's margin account (equity $99,882, buying power $394,414) it produced a nonsensical
"-295% deployed". Separately, the Portfolio-Greeks panel showed "Computed 1034m ago" with no
visual signal that the numbers were stale. This ticket computes a meaningful deployment figure
from position exposure over equity and adds a staleness badge driven by `greeks_fetched_at`.
Display-only — no buying-power caps, position-sizing, or execution logic touched.

## What was done

- `data/state_writer.py` — in `write_portfolio_snapshot`, replaced the margin-unsafe formula with
  `deployed_capital / equity * 100`, where `deployed_capital` sums each position's `market_value`
  (falling back to `abs(current_price * qty)` when `market_value` is absent). Field name
  `buying_power_used_pct` unchanged (frontend already binds to it); `equity == 0` guard preserved;
  `today_pnl`/`today_pnl_pct` untouched.
- `frontend/src/types/index.ts` — added `greeks_fetched_at?: string | null` to the `Portfolio` type.
- `frontend/src/pages/Dashboard.tsx` — `PortfolioGreeksCard` now accepts an optional `badge` node
  rendered next to its header.
- `frontend/src/pages/AccountDetail.tsx` — wires a `StalenessBadge` (reused as-is, using its real
  `last_updated`/`stale_after_days`/`label` prop shape) into that header, showing "not enriched"
  when `greeks_fetched_at` is null instead of a fake-fresh state.
- `tests/test_state_writer.py` — added `test_margin_account_deployment_is_non_negative` (equity
  100000, buying_power 400000, one $25000 position → asserts 25.0, not negative). Updated the
  pre-existing `test_produces_valid_json` expectation from `60.0` (old formula's output) to `0.0`
  (that fixture's position has no `market_value` and a negligible fallback value against its
  equity, under the corrected formula). `test_handles_zero_equity` already covered the
  divide-by-zero guard.

## Key files

- `data/state_writer.py` — `write_portfolio_snapshot`'s `bp_used_pct` computation (~line 152).
- `frontend/src/pages/AccountDetail.tsx`, `Dashboard.tsx`, `types/index.ts` — staleness badge wiring.
- `tests/test_state_writer.py` — margin-deployment + zero-equity coverage.

## Notes for future work

**Known duplicate not fixed here (deliberately out of scope):** `data/state_writer.py`'s
`write_account_snapshot` method (~line 321) contains the IDENTICAL `(1 - buying_power/equity)*100`
formula this ticket just replaced in `write_portfolio_snapshot`. It was left untouched per this
ticket's literal scope (confirmed via `git diff` — zero changes to that method). If anything
consumes `write_account_snapshot`'s output for display, it will still show the same
"-295%"-style bug. Worth a small follow-up ticket to apply the identical fix there, or to
consolidate both call sites onto one shared helper.

Gate: `pytest tests/test_state_writer.py -q` — 18 passed. `cd frontend && npm run build` — clean.
Per the ticket's "Post-merge (user)" note: on paper_1, confirm "deployed" now reads a sane
non-negative % and the Greeks panel shows a staleness badge when enrichment is old; whether the
17h-stale Greeks reflect a `portfolio_refresh` problem is a separate investigation.
