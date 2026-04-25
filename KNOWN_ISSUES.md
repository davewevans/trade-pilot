# Known Issues — trade-pilot

Tracker for open bugs and gaps that have been triaged but not yet fixed.
Organized by priority. Update when an issue is resolved (move to "Recently
Fixed" with the commit hash) or when new issues are surfaced.

**This file is the canonical priority list — if you're debugging something
and find it listed here, the team already knows about it.**

Last updated: 2026-04-25

---

## Open

### P1 — Observability gaps (block efficient debugging)

**#1 — Structured log capture committed but not writing files.**
- Symptom: Daily bundle "Errors & warnings" section shows "_No structured
  log file found for this date._"
- Likely causes: `STRUCTURED_LOG_CAPTURE_ENABLED` env var unset on Render,
  or the handler isn't being installed in the scheduler process.
- Investigation steps in the Friday-evening fix pack notes; check Render
  env vars before assuming code bug.
- Files involved: `utils/structured_log_handler.py`, `main.py`,
  `scheduler.py`, `config.py`.

**#2 — Daily bundle data source health shows everything ✗ failed.**
- Symptom: Bundle "Data source health" section reports every source as
  failed even though all sources fetched data successfully in the cycle.
- Likely cause: freshness threshold using wrong units (seconds vs ms) or
  comparing against a stale timestamp source.
- Cosmetic — does not affect trading.
- File involved: `api/daily_bundle.py` (`_section_data_health`).

**#3 — Daily bundle "no cycles found for this date" despite cycle running.**
- Symptom: Bundle cycle-summary section is empty while decisions clearly
  exist for the same date.
- Likely cause: `market_open` job isn't writing rows to the `cycles`
  SQLite table.
- Files involved: `jobs/market_open.py`, `database/recorder.py`.

**#4 — Daily bundle account list mixes accounts with strategy keys.**
- Symptom: Bundle shows `iron_condor`, `wheel`, `spreads`, `patterns`
  alongside `paper_1`–`paper_6`. Should be 6 accounts only.
- Cosmetic — accounts work correctly in the actual trading code.
- File involved: `api/daily_bundle.py` (`_section_header` or wherever
  open positions by account is computed).

### P1 — Performance / silent data loss

**#5 — Option chain pre-clamp committed but not effective in production.**
- Symptom: SPY context build still ~23s; `Option chain hit 500 contract
  cap` warning still recurs in 4/24 logs. Per Prompt 6 commit
  (0ea782e), expected SPY build time was <10s.
- Likely causes: `OPTION_CHAIN_STRIKE_PRECLAMP_ENABLED=false` on Render,
  or the clamp helper isn't wired into every `get_option_chain` call site.
- Files involved: `data/option_chain_utils.py`, `data/context_builder.py`,
  `brokers/alpaca_broker.py`.

### P2 — Deferred cleanup

**#7 — Plan B-2 (drop premium_richness_label / forecast_move_pct).**
- Status: queued. Plan documented in
  `plan-b-orats-field-mapping-cleanup.md` (operator's working notes).
- Touches: `data/orats_client.py`, `data/context_builder.py`,
  `prompts/wheel_idle.md`, `prompts/turnover_wheel_idle.md`,
  `prompts/iron_butterfly_idle.md`, `prompts/system.md`.
- Wait until at least 2 trading days of healthy A+B-1 operation
  confirmed via daily bundle.

**#8 — Plan B-3 (historical normalizer in `orats_historical.py`).**
- Status: queued. Variant 3a (drop the broken fields) confirmed safe
  by grep — no backtest reads `atm_iv_m*` or `skew_m*` from historical
  results.
- Lower priority — affects backtests, not live trading.
- File involved: `data/orats_historical.py::_normalize_summary`.

### P3 — Untested in production (not bugs yet)

**#13 — Wheel position-management path completely untested live.**
- Status: Bot has been 100% IDLE across all wheel symbols since launch.
- Path involved: `wheel_strategy.py` and `turnover_wheel_strategy.py`
  state transitions IDLE → SHORT_PUT → LONG_STOCK → SHORT_CALL → IDLE.
  Roll/close/assignment handling has never executed against real fills.
- Expect to find new bugs first time NVDA fills, then again first time
  it gets assigned, then again first time a CC gets called away.
- Not something to fix preemptively — something to expect.

**#14 — Spread lifecycle completely untested live.**
- Status: No spreads have opened in production.
- Path involved: `_spread_lifecycle.py` state transitions IDLE →
  PENDING_OPEN → OPEN → PENDING_CLOSE → CLOSED.
- Same expectation as #13.



---
#16 — yfinance structurally unreliable for ETF fundamentals. quoteSummary endpoint returns 404 on SPY/QQQ/IWM/GLD persistently across cycles. Affects: ex_dividend (safety implication for bear call spread guardrail — patched defensively in commit X), fundamentals (Claude context, degradation), VIX (no observed failures yet but same data source). Migration plan: VIX → FRED, ex_div → Finnhub, fundamentals → Finnhub, technicals → Alpaca. See yfinance migration plan doc when written.

## Recently Fixed

(Move items here when resolved with the commit hash that fixed them.
Trim entries older than 60 days during routine maintenance.)

- **2026-04-25** — IVR fix Part A: source `iv_rank_1y` from `/ivrank` instead of `/summaries`. Wheel was hard-skipping every cycle because `/summaries` does not return `ivRank1y` despite the parser reading `row.get("ivRank1y")`. Fixed in commit `a13cad5`.
- **2026-04-25** — IVR fix Part B-1: route `atm_iv_m*` from `/cores` instead of `/summaries`. Same class of bug as Part A; `iv_overvalued_label` was always None as a downstream consequence. Fixed in commit `1732069`.
- **2026-04-23** — Initial 8-prompt fix pack: `trade_journal` NoneType bug, ORATS cache type probe, ORATS cores defensive unpack, bull_put_spread instrumentation, Finnhub tier-aware short-circuit, option chain pre-clamp (committed but ineffective — see #5), structured log capture (committed but not writing — see #1), daily bundle endpoint.
