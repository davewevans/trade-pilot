# Structured Outputs Design — trade-pilot Claude Advisor

**Status:** Design review (Story 2A). Implementation follows in Story 2B once approved.

---

## 1. Schema Inventory — Separate Schemas Per Call

**Recommendation: one schema per (strategy, phase) pair, selected at call time.**

We have 4 strategies × up to 2 phases = 8 (strategy, phase) schemas plus 4 wheel phases = 12 schemas total. Each is small and fully independent.

The alternative — a single union schema with `oneOf` per strategy — is rejected for three reasons:

1. **Complexity limits.** The Anthropic API allows 24 optional parameters across all strict schemas in a request, and 16 parameter `union-types` variants. A full-coverage union would blow past both limits (iron condor alone has 5 leg-symbol fields + DTE + credits + strikes, most of which are only relevant to that strategy).
2. **Cache invalidation.** Any schema change invalidates the prompt cache for every strategy, not just the one that changed. Separate schemas mean a bull-put-spread schema update only cold-starts that strategy's cache on the next call.
3. **Simpler routing.** `ClaudeAdvisor.ask()` and `ask_spread()` already know the (strategy, phase) at call time. Selecting the schema is one dict lookup.

**Schema selection map:**

| Call path | strategy | phase | Schema key |
|---|---|---|---|
| `ask(phase=IDLE)` | wheel | idle | `wheel_idle` |
| `ask(phase=SHORT_PUT)` | wheel | short_put | `wheel_short_put` |
| `ask(phase=LONG_STOCK)` | wheel | long_stock | `wheel_long_stock` |
| `ask(phase=SHORT_CALL)` | wheel | short_call | `wheel_short_call` |
| `ask_spread("bull_put_spread", "idle")` | bull_put_spread | idle | `bull_put_spread_idle` |
| `ask_spread("bull_put_spread", "open")` | bull_put_spread | open | `bull_put_spread_open` |
| `ask_spread("bear_call_spread", "idle")` | bear_call_spread | idle | `bear_call_spread_idle` |
| `ask_spread("bear_call_spread", "open")` | bear_call_spread | open | `bear_call_spread_open` |
| `ask_spread("iron_condor", "idle")` | iron_condor | idle | `iron_condor_idle` |
| `ask_spread("iron_condor", "open")` | iron_condor | open | `iron_condor_open` |
| `ask_spread("long_call_vertical", "idle")` | long_call_vertical | idle | `long_call_vertical_idle` |
| `ask_spread("long_call_vertical", "open")` | long_call_vertical | open | `long_call_vertical_open` |

---

## 2. Field-by-Field Schema Definitions

### 2.1 Wheel IDLE

**Actions:** `sell_put` | `skip`

All fields read by `validate_csp_entry` → `_check_sell_put`: `action`, `symbol`, `qty`, `order_type`, `limit_price`.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "action", "symbol", "qty", "order_type", "limit_price",
    "reasoning", "confidence", "skip_reason"
  ],
  "properties": {
    "action":       { "type": "string", "enum": ["sell_put", "skip"] },
    "symbol":       { "type": ["string", "null"], "description": "OCC option symbol, null on skip" },
    "qty":          { "type": "integer", "minimum": 1 },
    "order_type":   { "type": "string", "enum": ["limit", "market"] },
    "limit_price":  { "type": ["number", "null"], "description": "Negative = credit received; null on skip" },
    "reasoning":    {
      "type": "object",
      "additionalProperties": false,
      "required": ["macro", "fundamental", "technical", "volatility", "selection", "risk"],
      "properties": {
        "macro":       { "type": "string" },
        "fundamental": { "type": "string" },
        "technical":   { "type": "string" },
        "volatility":  { "type": "string" },
        "selection":   { "type": "string" },
        "risk":        { "type": "string" }
      }
    },
    "confidence":   { "type": "string", "enum": ["high", "medium", "low"] },
    "skip_reason":  { "type": ["string", "null"] }
  }
}
```

Optional parameter count: 0 (all fields required). ✓

---

### 2.2 Wheel SHORT_PUT

**Actions:** `roll` | `close` | `hold` | `skip`

Read by `_check_sell_put` (roll: `symbol`, `qty`), `_check_sell_call` (close: `symbol`), universal (`action`, `qty`, `order_type`, `limit_price`).

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "action", "symbol", "qty", "order_type", "limit_price",
    "reasoning", "confidence", "skip_reason"
  ],
  "properties": {
    "action":       { "type": "string", "enum": ["roll", "close", "hold", "skip"] },
    "symbol":       { "type": ["string", "null"] },
    "qty":          { "type": "integer", "minimum": 1 },
    "order_type":   { "type": "string", "enum": ["limit", "market"] },
    "limit_price":  { "type": ["number", "null"] },
    "reasoning":    {
      "type": "object",
      "additionalProperties": false,
      "required": ["macro", "fundamental", "technical", "volatility", "selection", "risk"],
      "properties": {
        "macro":       { "type": "string" },
        "fundamental": { "type": "string" },
        "technical":   { "type": "string" },
        "volatility":  { "type": "string" },
        "selection":   { "type": "string" },
        "risk":        { "type": "string" }
      }
    },
    "confidence":   { "type": "string", "enum": ["high", "medium", "low"] },
    "skip_reason":  { "type": ["string", "null"] }
  }
}
```

Optional parameter count: 0. ✓

---

### 2.3 Bull Put Spread IDLE

**Actions:** `OPEN` | `SKIP`

Read by `validate_bull_put_spread_entry`: `action`, `limit_price`, `net_credit`, `max_loss`, `dte`, `short_put_symbol`, `long_put_symbol`.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "action", "short_put_symbol", "long_put_symbol", "expiration",
    "dte", "short_put_strike", "long_put_strike",
    "net_credit", "max_loss", "limit_price",
    "reasoning", "skip_reason"
  ],
  "properties": {
    "action":            { "type": "string", "enum": ["OPEN", "SKIP"] },
    "short_put_symbol":  { "type": ["string", "null"] },
    "long_put_symbol":   { "type": ["string", "null"] },
    "expiration":        { "type": ["string", "null"] },
    "dte":               { "type": ["integer", "null"], "minimum": 0 },
    "short_put_strike":  { "type": ["number", "null"] },
    "long_put_strike":   { "type": ["number", "null"] },
    "net_credit":        { "type": ["number", "null"], "description": "Positive dollar amount of credit received" },
    "max_loss":          { "type": ["number", "null"], "description": "Positive dollar amount of max loss" },
    "limit_price":       { "type": ["number", "null"], "description": "Negative = net credit; null on SKIP" },
    "reasoning":         {
      "type": "object",
      "additionalProperties": false,
      "required": ["macro", "fundamental", "technical", "volatility", "selection", "risk"],
      "properties": {
        "macro":       { "type": "string" },
        "fundamental": { "type": "string" },
        "technical":   { "type": "string" },
        "volatility":  { "type": "string" },
        "selection":   { "type": "string" },
        "risk":        { "type": "string" }
      }
    },
    "skip_reason":       { "type": ["string", "null"] }
  }
}
```

Optional parameter count: 0 (all nullable fields still listed as required — null is a valid value). ✓

---

### 2.4 Bull Put Spread OPEN

**Actions:** `HOLD` | `CLOSE`

Read by management path: `action`, `limit_price` (from prompt). Guardrails don't validate management decisions.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["action", "reasoning", "limit_price"],
  "properties": {
    "action":      { "type": "string", "enum": ["HOLD", "CLOSE"] },
    "reasoning":   {
      "type": "object",
      "additionalProperties": false,
      "required": ["macro", "fundamental", "technical", "volatility", "selection", "risk"],
      "properties": {
        "macro":       { "type": "string" },
        "fundamental": { "type": "string" },
        "technical":   { "type": "string" },
        "volatility":  { "type": "string" },
        "selection":   { "type": "string" },
        "risk":        { "type": "string" }
      }
    },
    "limit_price": { "type": ["number", "null"], "description": "Positive buyback price on CLOSE; null on HOLD" }
  }
}
```

Optional parameter count: 0. ✓

---

### 2.5 Iron Condor IDLE — Complexity Stress Test

**Actions:** `OPEN` | `SKIP`

Read by `validate_iron_condor_entry`: `action`, `total_credit`, `max_loss`, `limit_price`, `dte`, `put_short_symbol`, `put_long_symbol`, `call_short_symbol`, `call_long_symbol`.

This is the most field-heavy schema (9 leaf fields for legs alone). Field count: 13 top-level fields + 6 reasoning sub-fields = 19 total leaf fields. All required → **0 optional**. Well within the 24-optional limit.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "action",
    "put_short_symbol", "put_long_symbol",
    "call_short_symbol", "call_long_symbol",
    "expiration", "dte",
    "total_credit", "max_loss", "limit_price",
    "reasoning", "skip_reason"
  ],
  "properties": {
    "action":            { "type": "string", "enum": ["OPEN", "SKIP"] },
    "put_short_symbol":  { "type": ["string", "null"] },
    "put_long_symbol":   { "type": ["string", "null"] },
    "call_short_symbol": { "type": ["string", "null"] },
    "call_long_symbol":  { "type": ["string", "null"] },
    "expiration":        { "type": ["string", "null"] },
    "dte":               { "type": ["integer", "null"], "minimum": 0 },
    "total_credit":      { "type": ["number", "null"], "description": "Combined credit from both spreads, positive" },
    "max_loss":          { "type": ["number", "null"], "description": "Positive max loss amount" },
    "limit_price":       { "type": ["number", "null"], "description": "Negative = net credit; null on SKIP" },
    "reasoning":         {
      "type": "object",
      "additionalProperties": false,
      "required": ["macro", "fundamental", "technical", "volatility", "selection", "risk"],
      "properties": {
        "macro":       { "type": "string" },
        "fundamental": { "type": "string" },
        "technical":   { "type": "string" },
        "volatility":  { "type": "string" },
        "selection":   { "type": "string" },
        "risk":        { "type": "string" }
      }
    },
    "skip_reason":       { "type": ["string", "null"] }
  }
}
```

Optional parameter count: 0. ✓ Iron condor fits the limit comfortably even with 4 leg symbols.

---

### 2.6 Iron Condor OPEN

**Actions:** `HOLD` | `CLOSE`

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["action", "reasoning", "urgency", "limit_price"],
  "properties": {
    "action":      { "type": "string", "enum": ["HOLD", "CLOSE"] },
    "reasoning":   {
      "type": "object",
      "additionalProperties": false,
      "required": ["macro", "fundamental", "technical", "volatility", "selection", "risk"],
      "properties": {
        "macro":       { "type": "string" },
        "fundamental": { "type": "string" },
        "technical":   { "type": "string" },
        "volatility":  { "type": "string" },
        "selection":   { "type": "string" },
        "risk":        { "type": "string" }
      }
    },
    "urgency":     { "type": "string", "enum": ["immediate", "normal"] },
    "limit_price": { "type": ["number", "null"] }
  }
}
```

Optional parameter count: 0. ✓

---

## 3. The Reasoning Field Decision

**Decision: use the structured 6-field dict for ALL strategies, including spreads.**

Currently wheel uses `{"macro": ..., "fundamental": ..., "technical": ..., "volatility": ..., "selection": ..., "risk": ...}` and spreads use a flat string. This creates an inconsistency in the prompt, the DB schema (which tries to JSON-parse strings), and the API server.

The structured dict is the right choice for three reasons:

1. **Forces dimensional thinking.** Claude must address macro, fundamentals, technicals, volatility environment, candidate selection, and risk separately. A flat string lets it fuse all of these into a single sentence and leave gaps — exactly what we don't want on a trading bot.
2. **The DB already handles it.** `recorder.py` JSON-encodes dict reasoning on write; `DecisionRepository._row_to_dict` parses it back. Spread decisions that currently write a string will now write a parseable dict — a net improvement.
3. **Consistent schema.** One reasoning shape means one frontend component, one DB query, one downstream consumer.

Migration note: spread prompt files will need the flat `"reasoning": str` language replaced with the 6-field breakdown instructions. This is part of Story 2B.

---

## 4. Action Enum Strategy

**Decision: keep separate — wheel lowercase, spreads uppercase — for now.**

Standardizing would require updating every consumer of the action field. A full grep shows:

**Files that would need updating if we standardized to lowercase:**
- `database/recorder.py:27` — `_CYCLE_OPENING_ACTIONS = frozenset({"SELL_PUT", ...})` — normalizes with `.upper()` before matching, so this is already insulated
- `api/server.py:773` — `act = action.upper()` — this normalizes, so insulated
- `jobs/market_open.py:225` — `WHEEL_ENTRY_ACTIONS = ("sell_put", "sell_call")` — wheel specific, stays lowercase
- `main.py:155` — `if action in ("sell_put", "sell_call")` — wheel specific, stays lowercase
- `strategies/guardrails.py:118` — `if action == "sell_put"` — wheel specific
- `jobs/market_open.py` — spread action comparisons use uppercase `"OPEN"`, `"SKIP"`, `"CLOSE"`, `"HOLD"`

Changing spread actions to lowercase would require touching 8+ files and their tests. The risk-to-reward is poor given we're on paper trading with live schema churn ahead.

**Recommendation:** Keep the split. Document it in `ai/schemas.py` with a clear comment. Revisit if a future v2 schema rewrite lands.

---

## 5. Refusal / max_tokens Handling

When `stop_reason` is `"refusal"` or `"max_tokens"`, the response JSON is either absent or incomplete. The structured outputs guarantee only applies to successful completions.

**Specified fallback behavior:**

```python
# In ClaudeAdvisor.ask() and ask_spread():
if response.stop_reason in ("refusal", "max_tokens"):
    logger.error(
        "Claude stop_reason=%s — falling back to safe SKIP "
        "(strategy=%s phase=%s)", response.stop_reason, strategy, phase
    )
    return _safe_skip_response(strategy, phase, reason=response.stop_reason)
```

The `_safe_skip_response()` helper returns the existing safe-SKIP dict shape. The current retry loop (which handles parse failures) is removed entirely in Story 2B since structured outputs prevent the parse failures it was protecting against.

**When to log at ERROR vs WARNING:**
- `"refusal"` → ERROR. Claude declined the request. Investigate the prompt.
- `"max_tokens"` → ERROR. The response was truncated. Investigate token budget.
- Network/API exception → WARNING (existing behavior, already logged).

These should be rare. If they occur more than once in a session, it warrants immediate investigation.

---

## 6. Schema Versioning

**Where schemas live:** `ai/schemas.py` — a single module that exports a `get_schema(strategy, phase)` function and all schema dicts. This keeps schemas co-located with the advisor that uses them, versioned with the same git history.

**Structure:**
```
ai/
  schemas.py      ← all schema dicts + get_schema() selector
  claude_advisor.py
```

**Versioning strategy:** Schema changes are tracked via git. The `output_config.format` dict passed to the Anthropic API is hashed and included in the `claude_api_usage` log line (added in Story 2B) so we can correlate cache miss spikes with schema deployments.

**Rollback story:** If a schema change causes unexpected SKIPs or refusals in production:
1. `git revert` the schema commit
2. Redeploy — the cache cold-starts on the next call with the old schema
3. Investigate the prompt/schema mismatch offline

No migration table needed. The schema is a Python dict literal; diff it with `git show`.

**Do NOT version schemas as external JSON files.** Keeping them as Python dicts means the type checker, linter, and test suite all participate in catching typos. External JSON files are opaque to tooling.

---

## 7. Open Questions

**Q1: The iron condor idle prompt currently shows `{{iron_condor_candidate}}` as a placeholder that gets rendered into the prompt. Will the structured schema conflict with the rendered field names?**

The schema defines field *names in the response*, not field names in the prompt. The `{{iron_condor_candidate}}` block in the prompt is input context; Claude reads it and outputs `put_short_symbol`, `call_short_symbol`, etc. No conflict. Verify by checking what `iron_condor_candidate` renders to in context — if the field names there differ from the schema's `put_short_symbol` etc., update the prompt text in Story 2B.

**Q2: Bear call spread and long call vertical schemas are not detailed above. Should they follow the same pattern?**

Yes. Bear call spread IDLE mirrors bull put spread IDLE but with `short_call_symbol` / `long_call_symbol` / `net_credit`; long call vertical IDLE uses `long_call_symbol` / `short_call_symbol` / `net_debit`. Both follow the same pattern as sections 2.3/2.4. I've omitted them here for brevity — they're straightforward extensions.

**Q3: The `confidence` field currently appears in wheel schemas but not in spread schemas. Should spreads include it?**

Spreads don't currently pass `confidence` to the guardrails or the circuit breaker. Including it in the spread schema costs nothing (one more required field) and gives useful signal in the DB. **Recommendation: add it to all spread schemas as a required `"high" | "medium" | "low"` field in Story 2B.** Update the spread prompt files accordingly.

**Q4: Am I wrong to prefer the structured reasoning dict?**

I don't think so, but there's a real cost: it makes the spread prompt files more complex (they need to teach Claude the 6-field breakdown where today they accept a free-form string). If spread prompt quality degrades (more SKIPs, worse decisions), the flat string was better. Story 1's instrumentation will make this visible — watch `output_tokens` after Story 2B deploys. If output tokens increase significantly and decision quality doesn't improve, the dict reasoning added noise not signal.

**Q5: Will adding `output_config.format` invalidate the existing prompt cache?**

Yes. Anthropic auto-injects a system prompt addendum when `output_config.format` is present, which changes the cache key. Expect a full cache cold-start on the first call after deploy. Budget for the cache write cost. The second call should hit the cache normally. Note this in the Story 2B commit message so ops doesn't mistake the spike for a bug.
