# Claude costs panel reads token_usage instead of the always-NULL decisions column

**Status:** not started
**Autonomous:** yes
**Verify:** python -m pytest tests/test_claude_costs_token_usage.py -q

## Goal

The `/api/claude-costs` dashboard panel is empty across every window. Root cause (verified
against a live DB snapshot 2026-07-01): it aggregates `decisions.estimated_cost_usd`, but NO
production caller passes `usage_data` to `recorder.record_decision`, so that column is NULL on
all 864 decision rows. The real cost/token history lives in the `token_usage` table (written by
`recorder.record_token_usage`, called from `jobs/market_open.py` and `jobs/position_check.py`) —
380 rows, ~$13.47, Apr–Jul 2026. Repoint the endpoint's aggregation to `token_usage`, behind an
env kill switch, keeping the JSON response shape byte-identical so the frontend needs no change.

## Key files

- `config.py` — add the `CLAUDE_COSTS_SOURCE` setting (mirror the existing `ADVISOR_MODEL`
  env pattern, ~line 316–325).
- `api/server.py` — `_claude_costs_from_db(conn, window, strategy_types)` (~line 3120) is the
  ONLY consumer of the decisions-based cost query; the `/api/claude-costs` handler (~line 3277)
  calls it. Add a sibling `_claude_costs_from_token_usage` and switch the handler on the flag.
  Leave `_claude_costs_from_db` in place as the fallback. Reuse the existing `_WINDOW_DAYS`
  map, `_strategy_filter`, and the `_OPEN_ACTIONS`/`_CLOSE_ACTIONS`/`_SKIP_ACTIONS` sets.
- `database/repositories/token_usage_repository.py` — reference only (no change). Columns:
  `timestamp, strategy_type, underlying, model, input_tokens, output_tokens, cache_read_tokens,
  cache_creation_tokens, response_time_ms, estimated_cost_usd, decision_action`.
- `tests/test_claude_costs_token_usage.py` — new test (below).

## Implementation steps

1. **config.py** — directly below the `ADVISOR_MODEL` line add:
   ```python
   # Data source for /api/claude-costs. token_usage is the populated table;
   # "decisions" is the legacy (empty) source, kept as a fallback.
   self.CLAUDE_COSTS_SOURCE: str = os.getenv("CLAUDE_COSTS_SOURCE", "token_usage")
   ```
2. **api/server.py** — add `_claude_costs_from_token_usage(conn, window, strategy_types)`
   directly below `_claude_costs_from_db`. Do NOT edit `_claude_costs_from_db`. Return the SAME
   dict keys, sourced from `token_usage`:
   - current + prior window predicates on `timestamp` using the same `_WINDOW_DAYS` logic
     (`timestamp >= datetime('now', ?)`; prior window is the `days` immediately before; "all"
     → prior empty).
   - `total_cost_usd` / `prior_window_cost_usd`: `COALESCE(SUM(estimated_cost_usd), 0)`.
   - `decisions_count`: `COUNT(*)` in the current window (keep the key; it now counts Claude
     API calls, the correct denominator for a cost panel — add a one-line comment).
   - `filled_trades_count`: `COUNT(*) WHERE UPPER(decision_action) NOT IN ('SKIP','HOLD')`.
   - `cost_per_filled_trade_usd`: total_cost / filled_count, or None when filled_count == 0.
   - `cost_by_outcome`: GROUP BY `UPPER(decision_action)`, bucketed with the existing
     `_OPEN_ACTIONS`/`_CLOSE_ACTIONS`/`_SKIP_ACTIONS` sets; NULL/blank `decision_action` → "skip".
   - `cache_hit_rate`: `cache_read / (cache_read + cache_creation)`, None when denom == 0
     (same definition `_claude_costs_from_db` uses).
   - `by_model_version`: GROUP BY `model`, keyed under the existing `"by_model_version"` key.
   - Apply `strategy_types` with `strategy_type IN (...)`, including the
     `len(strategy_types) == 0 → all-zero dict` short-circuit that `_claude_costs_from_db` has.
3. **api/server.py** — in the `/api/claude-costs` handler, choose by the flag; keep the existing
   try/except/finally and the `conn is None` early return unchanged:
   ```python
   if settings.CLAUDE_COSTS_SOURCE == "token_usage":
       return _claude_costs_from_token_usage(conn, window, _strategy_filter(account))
   return _claude_costs_from_db(conn, window, _strategy_filter(account))
   ```
4. **.env.example** — add `CLAUDE_COSTS_SOURCE=token_usage` near `ADVISOR_MODEL`.
5. **tests/test_claude_costs_token_usage.py** — seed an in-memory SQLite `token_usage` table
   with rows across models and `decision_action` values, some inside and some outside a 7d
   window, and assert `_claude_costs_from_token_usage` returns: non-zero `total_cost_usd`,
   correct `by_model_version` keys, correct open/close/skip bucketing, working prior-window and
   `strategy_types` filters. Boundary cases: a row exactly at the window edge; `decision_action`
   NULL → lands in "skip" and is excluded from `filled_trades_count`; empty `strategy_types`
   list → all-zero dict.

## Constraints

- **Response JSON keys must stay byte-identical** — the frontend `ClaudeCostsResponse` type and
  `ClaudeCostsCard` depend on the exact shape. No renames.
- Do NOT modify the recording path (`record_decision` / `record_token_usage`), the `decisions`
  or `token_usage` schemas, or backfill any data.
- Do NOT edit `_claude_costs_from_db` — it stays as the `CLAUDE_COSTS_SOURCE=decisions` fallback.
- Ship behind the flag (default `token_usage`) so it's revertible via env without a deploy.

## Verification

- `python -m pytest tests/test_claude_costs_token_usage.py -q` passes.
- `grep -n "_claude_costs_from_db\|_claude_costs_from_token_usage" api/server.py` shows both
  functions present and the handler switching on `settings.CLAUDE_COSTS_SOURCE`.
- Sanity (manual, post-merge): `/api/claude-costs?window=all` returns `total_cost_usd > 0`
  against a DB with token_usage rows.
