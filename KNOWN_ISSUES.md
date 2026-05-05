# Known Issues — trade-pilot

Tracker for open bugs and gaps that have been triaged but not yet fixed.
Organized by priority. Update when an issue is resolved (move to "Recently
Fixed" with the commit hash) or when new issues are surfaced.

**This file is the canonical priority list — if you're debugging something
and find it listed here, the team already knows about it.**

Last updated: 2026-04-28

---

## Open

### P1 — Observability gaps (block efficient debugging)

**#1 — Structured log capture committed but not writing files.**
- Symptom: Daily bundle "Errors & warnings" section shows "_No structured
  log file found for this date._"
- Investigation (2026-04-27): original hypotheses ruled out. Env var
  confirmed true on Render. Writer (`main.py`, `api/run.py`) and reader
  (`api/server.py`) both use `settings.STRUCTURED_LOG_DIR` (defaults to
  `/data/snapshots/logs`). Render persistent disk is mounted at `/data` —
  path is covered. Wiring looks correct in both processes.
- Most likely remaining cause: stale Render deploy (wiring shipped
  2026-04-23; Render may not have redeployed since).
- Added INFO diagnostic at handler install time (commit `422c4eb`). After
  next redeploy, grep Render logs for `"Structured log handler installed:"`.
  If the line doesn't appear, the handler isn't being reached; if it
  appears with the right path, the issue is elsewhere.
- Files involved: `utils/structured_log_handler.py`, `main.py`,
  `api/run.py`, `config.py`.

**#2 — Daily bundle data source health shows everything ✗ failed.**
- Symptom: Bundle "Data source health" section reports every source as
  failed even though all sources fetched data successfully in the cycle.
- Likely cause: freshness threshold using wrong units (seconds vs ms) or
  comparing against a stale timestamp source.
- Cosmetic — does not affect trading.
- File involved: `api/daily_bundle.py` (`_section_data_health`).

**#3 — (moved to Recently Fixed — was a documentation bug, not a recorder bug)**

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

**#17 — yfinance package cleanup (post-Stage-6 follow-up).**
- Status: queued. Requires 3+ trading days of production soak on Stage 6 first.
- Update (2026-04-27): added 3-attempt exponential backoff before yfinance
  fallback for both FRED VIX and Alpaca corp-actions (commit `7797904`).
  Transient failures that previously triggered fallback will now retry; the
  soak clock should run more cleanly going forward.
- Pre-merge check: confirm production logs show zero `"falling back to yfinance"`
  messages from `get_ex_dividend_date` over the prior 3+ trading days. If the
  fallback HAS fired, investigate Alpaca corp-actions reliability before removing.
- Scope: remove `_get_ex_dividend_yfinance()` helper from `data/market_data.py`,
  remove `import yfinance as yf` line, remove `yfinance` from `requirements.txt`,
  add `data/market_data.py` to `SCOPE_LOCKED_FILES` in
  `tests/test_no_yfinance_in_migrated_paths.py`.
- Estimated size: ~30 lines deleted, one config change, one test edit.
- Files involved: `data/market_data.py`, `requirements.txt`,
  `tests/test_no_yfinance_in_migrated_paths.py`.

**#20 — Bear call spread ex-div guardrail tightening (deferred decision).**
- Status: queued, decision pending operator review.
- Context: Stage 3 introduced `ex_dividend_data_available: False/True`. The bear
  call spread guardrail currently passes when this is `False` (fail-OPEN). With
  Alpaca Corporate Actions as primary, valid empty results are now common (e.g.,
  GLD and non-dividend stocks return empty legitimately). Distinguishing
  "fetch failed" (`False`) from "no upcoming dividend" (`True`, all-None fields)
  is now reliable.
- Decision needed: tighten to fail-CLOSED when `ex_dividend_data_available=False`?
  Trade-off: safer (no entry on data-unknown) vs more conservative (skipped entries
  on transient Alpaca outages).
- Lower priority — current behavior matches pre-migration; this is a strengthening
  opportunity, not a regression.
- File involved: `strategies/guardrails.py`.

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

**#18 — Stage 6 backtest comparison verification owed.**
- Status: pending operator manual verification.
- Symptom: Stage 6 (backtester + CAHOLD migration) shipped with passing test suite
  but no end-to-end equivalence check against pre-migration backtest results.
- Action: pick a previously-run backtest config (e.g., `bull_put_spread` on
  SPY/AAPL for 2024), run with migrated code, compare total trade count + win rate
  + total P&L against pre-migration result. Within ±5% on each = fine; outside
  that = investigate.
- Most likely divergence sources in order: (a) Alpaca `Adjustment.ALL` vs yfinance
  `auto_adjust=True` dividend handling; (b) FRED `VIXCLS` vs yfinance `^VIX` close;
  (c) date alignment between Alpaca UTC bars and FRED date-only series.
- Until completed, treat backtest output as not yet validated end-to-end.
- File involved: `backtesting/engine.py`.

**#19 — Stage 6 CAHOLD live-path smoke test owed.**
- Status: pending operator manual verification.
- Symptom: `ContextBuilder.detect_support_bounce()` migrated from yfinance to
  Alpaca; tests pass but the live path feeding `long_call_vertical` entry decisions
  has not been smoke-tested in REPL or production.
- Action: `from data.context_builder import ContextBuilder; print(ContextBuilder.detect_support_bounce("AAPL"))`.
  Confirm `current_close` matches broker UI value within $1, `low_day_high` and
  `low_day_date` look sane. If results look off, the column-name lowercase change
  or 90-day Alpaca window is the suspect.
- Until completed, long_call_vertical may silently produce no entry signals in
  production.
- File involved: `data/context_builder.py`.

---

## Recently Fixed

(Move items here when resolved with the commit hash that fixed them.
Trim entries older than 60 days during routine maintenance.)

- **2026-04-28** — MACRO_EVENT_PROXIMITY fired live for the first time. FOMC scheduled for 2026-04-29 14:00 ET triggered `market_open` early-exit at 10:00 ET; zero entry decisions issued, zero LLM entry-cycle tokens consumed. First production validation of the Tier 3 macro-event blocker. No code change — observation only.
- **2026-05-05** — DB-lock contention root-caused and architecturally fixed. The scheduler process previously opened three independent SQLite connections (Database, ApiLedger, ORATSCache) that contended for the WAL writer slot during high-rate phases of market_open (56 `ApiLedger.record` failures observed in a 63-second window). Fixed by collapsing to one `sqlite3.Connection` per process via a `get_db()` singleton in `database/db.py`; ApiLedger and ORATSCache now accept injected connections. The API server (`api/server.py`, `api/daily_bundle.py`) and backtesting sweep (`research/backtesting/sweep.py`) were also migrated so no production code calls `sqlite3.connect()` outside `database/db.py`. Cross-process contention (scheduler ↔ API server) is handled by `busy_timeout=30000` + existing `@db_retry`. The 2026-04-27 `@db_retry` wrap on `ApiLedger._insert` (commit `f063a4a`) was symptom-suppression that converted silent drops into noisy retry-then-drop; it remains in place as belt-and-suspenders for cross-process locks. Commit `b6d169f`.
- **2026-04-27** — `ApiLedger._insert` silent row drops under parallel context builds (superseded band-aid — see 2026-05-05 entry). `_insert` was not retrying on SQLite "database is locked"; wrapped with `@db_retry(max_attempts=5, base_delay=0.05)`. This converted silent drops into noisy retry-then-drop but did not fix the root cause (intra-process multi-connection contention). Commit `f063a4a`.
- **2026-04-27** — FRED VIX and Alpaca corp-actions falling back immediately on transient errors. Each transient failure cost ~10s of wall time (Alpaca timeout) and reset the #17 yfinance soak clock. Added 3-attempt exponential backoff (1s/2s/4s) before the yfinance fallback. Commit `7797904`.
- **2026-04-27** — Daily bundle cycle-summary empty-state misleading (#3). Root cause hypothesis in this file was wrong: cycles are never written for SKIP/HOLD decisions by design, so zero cycles after 100% IDLE launch is correct. The actual bug was `_section_cycle_summary` showing "_No cycles found for this date._" which implied failure. Now renders two sub-sections ("Opened today" / "Active cycles from prior days") with accurate empty-state messages pointing to the Decisions section. Commit `26b74b9`.
- **2026-04-27** — market_open "30-minute lag" was not a bug (#9). Scheduled time is `10:00 ET`; `14:00 UTC = 10:00 EDT` (UTC-4 in April). The bot ran exactly on schedule. `config.SCHEDULE` comment explains the intentional 10:00 vs 09:30 choice (narrower bid-ask spreads after the opening rush). Added boot-time schedule audit logging. Commit `03d744e`.
- **2026-04-26** — yfinance migration complete (issue #16 split). All six stages shipped: VIX → FRED VIXCLS, VIX term structure deleted (zero callers), ex-dividend → Alpaca Corporate Actions, fundamentals → Finnhub `/stock/profile2` + `/stock/metric` (decomposed, ETF-aware), earnings yfinance fallback deleted (Finnhub-only), backtester + CAHOLD → Alpaca + FRED. Zero new vendors introduced. One yfinance call site intentionally retained (ex-div defensive fallback) — see #17.
- **2026-04-25** — IVR fix Part A: source `iv_rank_1y` from `/ivrank` instead of `/summaries`. Wheel was hard-skipping every cycle because `/summaries` does not return `ivRank1y` despite the parser reading `row.get("ivRank1y")`. Fixed in commit `a13cad5`.
- **2026-04-25** — IVR fix Part B-1: route `atm_iv_m*` from `/cores` instead of `/summaries`. Same class of bug as Part A; `iv_overvalued_label` was always None as a downstream consequence. Fixed in commit `1732069`.
- **2026-04-23** — Initial 8-prompt fix pack: `trade_journal` NoneType bug, ORATS cache type probe, ORATS cores defensive unpack, bull_put_spread instrumentation, Finnhub tier-aware short-circuit, option chain pre-clamp (committed but ineffective — see #5), structured log capture (committed but not writing — see #1), daily bundle endpoint.
