# Advisor prompt-caching cost tune-up (`1002-advisor-caching-cost-tuneup`)

**Status:** done
**Synthesized:** 2026-07-03

## Purpose

Closed two cost-tracking blindspots and added one small, flag-gated caching improvement in the
advisor. `claude-sonnet-5` calls (7 of the most recent 383) were recording
`estimated_cost_usd=NULL` because `CLAUDE_PRICING` had no entry for that model. Separately,
`scripts/cache_stats.py` hardcoded stale per-model pricing constants that would silently drift out
of date. Both are now fixed, plus a default-off flag to cache the advisor's per-phase instruction
block for a small additional hit-rate gain.

## What was done

- `config.py` — added a `claude-sonnet-5` entry to `CLAUDE_PRICING` (input 2.00, output 10.00,
  cache_read 0.20, cache_write 4.00 — the 1-hour rate, matching the advisor's `ttl:"1h"` system
  cache) and a new default-off `CACHE_INSTRUCTIONS_BLOCK` env-bool flag.
- `ai/claude_advisor.py` — added `ClaudeAdvisor._build_user_content(instructions, context_json)`:
  flag off returns the old single-string payload byte-identical; flag on returns a 2-block list
  with the instructions block carrying a 5m ephemeral `cache_control` breakpoint ahead of the
  uncached context block. Wired into `ask`, `ask_turnover_wheel`, `ask_spread`, replacing each
  inline `user_content` construction. `system=[...]`, `output_config`, `max_tokens`, and
  parsing/skip logic untouched.
- `scripts/cache_stats.py` — replaced four hardcoded `_PRICE_*` constants with a
  `config.get_pricing(settings.ADVISOR_MODEL)` lookup, falling back to `claude-sonnet-4-6` rates
  plus a printed warning if the active model is missing from `CLAUDE_PRICING`.
- `tests/test_claude_advisor.py` — added 3 tests: flag-off byte-identical shape, flag-on 2-block
  `cache_control` shape, and `get_pricing("claude-sonnet-5")` key coverage.

First implementation attempt made no file changes at all (misread the task); retried with a
fresh subagent given explicit failure context, which implemented correctly on the second pass.

## Key files

- `config.py` — `CLAUDE_PRICING["claude-sonnet-5"]`, `CACHE_INSTRUCTIONS_BLOCK` flag.
- `ai/claude_advisor.py` — `_build_user_content`, wired into `ask`/`ask_turnover_wheel`/`ask_spread`.
- `scripts/cache_stats.py` — pricing now sourced from `config.get_pricing`.
- `tests/test_claude_advisor.py` — new coverage for the flag and pricing lookup.

## Notes for future work

Gate: `pytest tests/test_claude_advisor.py tests/test_claude_cost_tracking.py
tests/test_claude_costs_token_usage.py -q` — 46 passed. Per the ticket's own "Post-merge (user)"
note: before merging `development → main`, confirm the four `claude-sonnet-5` rates against the
live Anthropic pricing page (introductory pricing cuts over 2026-08-31) and update the dated
comment if any differ. To actually enable instruction caching, set `CACHE_INSTRUCTIONS_BLOCK=true`
and compare `scripts/cache_stats.py` hit rate before/after.
