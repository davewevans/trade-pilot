# Claude costs panel reads token_usage instead of the always-NULL decisions column (`1001-claude-costs-read-token-usage`)

**Status:** done
**Synthesized:** 2026-07-01

## Purpose

The `/api/claude-costs` dashboard panel was empty across every window because it aggregated
`decisions.estimated_cost_usd`, a column no production caller ever populates (no caller passes
`usage_data` to `recorder.record_decision`). The real cost/token history lives in the
`token_usage` table (~380 rows, ~$13.47, Apr–Jul 2026), written by `recorder.record_token_usage`.
This ticket repoints the panel's aggregation to `token_usage` behind an env kill switch, keeping
the response shape byte-identical so no frontend change is needed.

## What was done

Most of the substantive implementation (the `CLAUDE_COSTS_SOURCE` config flag, the new
`_claude_costs_from_token_usage` aggregator in `api/server.py`, the handler switch, and the full
test suite) had already been implemented and merged directly to `development` in a prior session
(commit `130e7cd`), independent of this ticket's creation. When this ticket ran through the
autonomous runner, the implementation subagent verified that state matched the ticket's spec
exactly and found only one gap: `.env.example` was missing the `CLAUDE_COSTS_SOURCE=token_usage`
documentation line called for in step 4. That single line was added; nothing else changed.

## Key files

- `config.py:329` — `CLAUDE_COSTS_SOURCE` setting (default `"token_usage"`), below `ADVISOR_MODEL`.
- `api/server.py:3120` — `_claude_costs_from_db` (unmodified, kept as the `decisions` fallback).
- `api/server.py:3277` — `_claude_costs_from_token_usage`, the new token_usage-backed aggregator.
- `api/server.py:3465-3467` — `/api/claude-costs` handler, branches on `settings.CLAUDE_COSTS_SOURCE`.
- `tests/test_claude_costs_token_usage.py` — 9 tests covering windows, boundaries, null actions,
  bucketing, cache hit rate, model grouping, strategy filters, and the "all" window.
- `.env.example` — added `CLAUDE_COSTS_SOURCE=token_usage` near `ADVISOR_MODEL` (this run's only change).

## Notes for future work

Gate: `pytest tests/test_claude_costs_token_usage.py -q` — 9 passed. Manual sanity check
(`/api/claude-costs?window=all` returning `total_cost_usd > 0`) is still a post-merge, human
verification step per the ticket — not automatable in this gate.
