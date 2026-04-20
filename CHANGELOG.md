# Changelog

All notable changes to trade-pilot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.9.0] - 2026-04-19

### Added
- **Broker-Truth State Reconciliation**: two-phase reconciliation subsystem that compares Alpaca broker positions, cash, and open orders against local state (`strategy_states` SQLite table, `wheel_state.json`, `turnover_wheel_state.json`, `open_spreads.json`). Phase 1 runs once at boot (`startup_broker_reconcile`); Phase 2 runs every 5 minutes during market hours (`drop_copy_reconcile`). Severity is "yellow" when any position delta exceeds `DROP_COPY_POS_MISMATCH_USD` / `DROP_COPY_POS_MISMATCH_PCT` or cash delta exceeds `DROP_COPY_CASH_MISMATCH_USD`; "red" when an untracked filled order is detected or delta exceeds `STARTUP_RECONCILE_HALT_THRESHOLD_USD`. Defaults to `DROP_COPY_ENFORCEMENT_MODE=log_only` — diffs are computed and written to reports but no state is mutated and no HALTED.lock is written. Flip to `enforce` only after a clean log-only week.
- `jobs/_broker_snapshot.py`: shared broker fetch layer; groups strategies by credential pair, captures per-account failures without aborting.
- `jobs/_reconcile_logic.py`: pure helpers — `derive_wheel_state_from_positions()`, `reconstruct_spread_identities()`, `classify_untracked_positions()`.
- `jobs/_reconcile_diff.py`: pure diff engine producing `ReconcileDiff` with severity, position/cash/order sub-diffs, and per-item deltas.
- `jobs/startup_broker_reconcile.py`: boot-time reconcile; writes `data/startup_reconcile_report.json`; in enforce+red mode writes HALTED.lock and `main.py` exits 1.
- `jobs/drop_copy_reconcile.py`: 5-minute market-hours reconcile; writes `data/drop_copy_last_report.json`; grace period (`DROP_COPY_GRACE_CYCLES`, default 2 cycles) before any action; in enforce+yellow mode writes `data/drop_copy_block.json` to suppress new entries; clears block when severity returns to "none".
- `SkipReason.DROP_COPY_BLOCK` skip code: gates new entries in `market_open.py` when `drop_copy_block.json` is present without halting management cycles. Visible in the Skip Reasons UI under the Circuit Breaker category.
- "Drop-copy block" row added to the Circuit Breaker section of the Skip Reasons frontend page.

- **Fill Realism Measurement (Shadow Execution)**: measurement-only subsystem that captures real-time NBBO (via ORATS primary, Alpaca fallback) at order submission and at +30s, +2min, +15min, and EOD, then classifies each snapshot as `always_fillable`, `sometimes_fillable`, `not_fillable`, or `data_unavailable`. Results aggregate into a per-strategy fill-realism score visible on the Strategy Health page. Gated by `SHADOW_EXECUTION_ENABLED` env var (default true). This is a blended signal — paper limits use Alpaca's 15-min-delayed data but are measured against real-time NBBO — so `not_fillable` does not isolate any single failure mode (strategy over-reaching vs. stale feed vs. true illiquidity).
- `shadow_executions` and `shadow_execution_legs` SQLite tables with per-snapshot bid/mid/ask and classification columns.
- `ShadowExecutionRepository` with `insert_submission`, `get_due`, `update_capture`, `recompute_completed`, and `get_fill_realism_aggregates`.
- `data/shadow_execution.py`: `classify_fillability()` pure function, `record_submission()` hook (call at order placement), `fetch_leg_quotes_for_capture()` for the follow-up job.
- `jobs/shadow_capture.py`: 1-minute interval job that processes pending t30s/t2m/t15m/EOD rows; permanent failure after 1 trading day or 5 attempts; all exceptions swallowed.
- Hooks in `main.py` (`execute_decision`) and all spread strategies (`bull_put_spread`, `bear_call_spread`, `iron_condor`, `iron_butterfly`, `long_call_vertical`) at entry and exit. Calendar spread explicitly excluded (`# NOT YET ACTIVE`).
- `GET /api/fill-realism?days=90` endpoint returning per-strategy aggregates; returns 404 when flag is off.
- `shadow_execution_enabled` field added to `GET /api/health` response.
- Fill Realism summary panel on the Strategy Health frontend page, color-coded by gate threshold (green ≥ gate%, yellow ≥ gate%−20, red below; gray "insufficient data" below gate sample).
- `FILL_REALISM_GATE_SAMPLE` (100) and `FILL_REALISM_GATE_PCT` (80.0) constants in `config.py`.

## [1.8.0] - 2026-04-19

### Added
- **Turnover Wheel strategy wired end-to-end**: `TurnoverWheelStrategy` is now fully instantiated and dispatched in `market_open.py` using its own Paper6 Alpaca account, mirroring the Standard Wheel call chain.
- `ContextBuilder.build()` gains a `strategy_name` parameter (default `"wheel"`) that selects the correct state file for cost-basis injection; supports `"wheel"` and `"turnover_wheel"`.
- `underlying_price_at_entry` field added to `TurnoverWheelStrategy` state file and surfaced in context — anchors the 20% drawdown roll gate to the original CSP entry price.
- `ClaudeAdvisor.ask_turnover_wheel()` method loads `prompts/turnover_wheel_*.md` and calls the Turnover Wheel schema aliases.
- `turnover_wheel_*` schema aliases registered in `ai/schemas.py` (same objects as the wheel schemas — no duplication).
- `TURNOVER_WHEEL_ENABLED` feature flag (env var, default `True`) gates the router and dispatch block.
- Turnover Wheel entry added to `_ACCOUNT_STRATEGY_MAP` in `api/server.py`.
- 26 new tests in `tests/test_turnover_wheel.py` covering schemas, router flag, state persistence, and advisor prompt routing.

### Fixed
- Removed counterfactual check ("if not already open, would you open it?") from all three Turnover Wheel management prompts — it incorrectly forced CLOSE on ITM covered calls heading to successful assignment.
- `turnover_wheel_long_stock.md`: replaced nonexistent `turnover_wheel_cost_basis` context key reference with the correct `wheel_cost_basis`; fixed all bare `cost_basis` references to `effective_cost_basis`.
- `turnover_wheel_short_call.md`: replaced vague "OTM enough" ex-dividend rule with an explicit dividend-vs-extrinsic calculation; split into separate checks for the current CC and any roll candidate; removed `delta within 0.45` roll cap (contradicted the NO delta cap rule for CC entry).
- `turnover_wheel_short_put.md`: grounded the 20% drawdown roll gate in the `underlying_price_at_entry` context field.
- `turnover_wheel_idle.md`: replaced vague macro check with explicit `confirmed_market_regime`, `regime_stable`, `macro.vix`, and `macro.fear_greed_score` field references.
- Added ex-dividend awareness to `turnover_wheel_long_stock.md` for new CC selection.

### Removed
- `TurnoverWheelStrategy.build_context()` and `_filter_chain()` deleted (dead code — strategy now routes through `ContextBuilder`).

## [1.7.1] - 2026-04-19

### Changed
- About page: replaced "Three Accounts" section with a six-strategy overview (Wheel, Turnover Wheel, Iron Condor, Bull Put Spread, Bear Call Spread, Long Call Vertical); removed all account-name references; corrected Python version from 3.11+ to 3.14.2.

## [1.7.0] - 2026-04-19

## [1.6.0] - 2026-04-18

### Added
- **ORATS quota hardening — 9-phase protection system** (see `docs/api_quota_protection.md`)
  following the 2026-04-17 incident where three concurrent processes exhausted the 20,000-call
  monthly ORATS budget in ~7 minutes.

  - **Phase 1 — Process Singleton Lock** (`utils/process_lock.py`): PID lock files prevent
    concurrent job instances. Stale locks (dead PID) are auto-cleared. `main.py` exits 2 if
    a live Python process already holds the lock.

  - **Phase 2 — API Usage Ledger & Hard Caps** (`data/api_ledger.py`): Every ORATS call
    recorded in `api_usage_ledger` (SQLite WAL). `check_and_reserve()` enforces rolling monthly
    (14k), daily (14k), and per-minute (600) caps for `orats_historical`; separate caps for
    `orats_live`. Raises `OratsQuotaExceeded` when exceeded. Cap values overridable via env vars.

  - **Phase 3 — Pre-flight Budget Check** (`jobs/weekly_research.py`): Estimates sweep cost
    before `run_sweep()` starts. `sys.exit(3)` if warm estimate exceeds remaining budget or 80%
    of it. `ORATS_ALLOW_BUDGET_HEAVY=1` overrides the 80% gate. Fires ntfy on abort.

  - **Phase 4 — Structured API Call Log** (`utils/json_log_formatter.py`): One JSON line per
    API call in `LOG_DIR/api_calls.jsonl` (14-day rotation). `propagate=False` keeps it out of
    `trade-pilot.log`. `data.orats_historical` and `data.orats_client` silenced to WARNING.

  - **Phase 5 — Usage Snapshot & API Endpoint** (`data/api_ledger.py`, `api/server.py`):
    `write_snapshot()` atomically writes `SNAPSHOTS_DIR/api_usage.json` every 50 billable calls,
    on quota exceeded, and at process exit. `GET /api/usage` exposes the snapshot.

  - **Phase 6 — ntfy Threshold Alerts** (`data/api_ledger.py`): Fires ntfy critical at 50%,
    75%, 90%, 95% monthly usage (once per threshold per month). Immediate alert on
    `OratsQuotaExceeded`; minute-cap alerts rate-limited to one per 5 minutes.

  - **Phase 7 — Kill Switch** (`data/api_ledger.py`, `api/server.py`): `ORATS_DISABLED.lock`
    in `DATA_DIR` instantly blocks all ORATS calls (`OratsDisabled` raised before cap checks).
    Admin endpoints: `POST /api/admin/orats/disable`, `POST /api/admin/orats/enable`,
    `GET /api/admin/orats/status`.

  - **Phase 8 — Sweep Resume Safety** (`research/backtesting/sweep.py`): Stale in-progress
    sweep state (state file age >1 h, no `completed_at`) is auto-reset with WARNING log.
    `run_sweep(force_restart=True)` bypasses. CLI: `--force-restart-sweep` /
    `FORCE_RESTART_SWEEP=1`.

  - **Phase 9 — Documentation** (`docs/api_quota_protection.md`): Full runbook including
    architecture diagram, env var reference, admin commands, and monitoring queries.

## [1.5.0] - 2026-04-17

### Added
- **Prompt 3-alt — Notification layer** (`notifications/`) — Severity-routed push notification
  system with ntfy primary backend and JSONL digest accumulator. `notify(severity, title, message)`
  routes "critical" to ntfy + digest, "warning"/"info" to digest only. Non-blocking: all exceptions
  are swallowed so a failed notification never breaks the calling code. `NTFY_TOPIC`, `NTFY_SERVER`,
  and `ALERT_FILLS` env vars added to `config.py` and `.env.example`.
- **Prompt 3-alt — Circuit-breaker state notifications** — `CircuitBreaker.update()` fires
  "critical" on RED transition and "warning" on YELLOW transition. `_write_halt_lock()` fires
  "critical" when the HALTED.lock file is written. Notifications use `_prev_status_str` guard
  so repeated calls at the same state do not spam.
- **Prompt 3-alt — Portfolio equity aggregation** (`CircuitBreaker.calculate_portfolio_equity`) —
  Static method that sums `portfolio_value` across all three trading accounts (wheel, iron_condor,
  spreads). Returns `None` if any account call fails so the CB never receives partial equity.
  `PORTFOLIO_ACCOUNTS` constant added to `strategies/circuit_breaker.py`.
- **Prompt 3-alt — Startup order reconciler** (`jobs/startup_reconciler.py`) — Reconciles
  PENDING_* spread tracker state and SQLite pending trades against live Alpaca order status at
  boot. HALTED.lock does not gate this job. Returns a summary dict. No order-creation calls.
- **Prompt 3-alt — Job crash notifications** — `scheduler.safe_run()` now fires a "critical"
  ntfy notification with a 500-char traceback excerpt on any unhandled job exception.
- **Prompt 3-alt — ORATS consecutive failure alerting** — Module-level
  `_consecutive_failures` counter in `data/orats_client.py` fires a "warning" notification after
  3 consecutive failures on any ORATS endpoint. Resets to 0 on success.

### Changed
- **Prompt 3-alt — Equity aggregation in market_open and position_check** — Both jobs now call
  `CircuitBreaker.calculate_portfolio_equity(make_broker)` instead of the previous per-account
  loop with fallback. If aggregation fails, CB state is preserved from the last successful update
  and a "warning" notification is fired.
- **Prompt 3-alt — Startup reconciler wired to main.py** — `validate_startup()` now calls
  `jobs.startup_reconciler.run()` after the snapshot job; summary is logged and a "critical"
  liveness notification fires on every bot start.

### Fixed


- **R4 UI — Skip-reason-by-gate dashboard** — `DecisionRepository.get_skip_breakdown()` aggregates
  SKIP decisions by gate and reason code for a configurable time window. New `GET /api/decisions/skip-breakdown`
  endpoint. `SkipReasons.tsx` rewritten: account/window filters, horizontal Recharts BarChart clickable by gate,
  unclassified callout, reason-code table. Static reference content collapsed into a `<details>` block.
- **R2 UI — Recommendation accuracy scorecard** — `ScorecardRepository.get_scorecard()` computes the
  4-cell accepted/rejected × add/remove matrix, joining `watchlist_recommendations` and `recommendation_outcomes`.
  New `GET /api/research/recommendations/scorecard` endpoint. `RecommendationScorecard.tsx` component renders
  a 2×2 grid with LIVE DATA (solid border) vs PROXY EST. (dashed border) visual distinction. Added as new
  "Scorecard" tab in `Research.tsx`.
- **R3 — Staleness badges** — `StalenessBadge.tsx` shared component shows "Updated today / Nd ago / Stale — Nd old"
  with red highlight past a configurable threshold. New `GET /api/research/last-run` endpoint reads
  `research_last_run.json` and queries MAX timestamps from all four research tables. Applied to Research.tsx
  (scan staleness) and Recommendations.tsx header.
- **R6 — ORATS cost telemetry** — `ORATSClient` gains `_track_call()`, `get_usage()`, and `reset_usage()`
  methods; all six HTTP-making methods now call `_track_call` with the endpoint name. `ORATSUsageTracker`
  (`data/orats_usage_tracker.py`) appends per-run records to a JSONL file and supports daily aggregation.
  `jobs/weekly_research.py` attempts to record usage after phases 1, 2, and 4. New `GET /api/orats/usage`
  endpoint. `DataSources.tsx` gains an "ORATS API Usage" section with StatCards, a Recharts LineChart,
  and a recent-runs table.

### Changed
- **R8 — EV-based win-rate gate** — `_winrate_to_multiplier` and `_winrate_to_tier_label` now
  accept `avg_pnl` as a second parameter. Win rate < 30% with positive average P&L is downgraded
  to `(0.7, 'poor')` instead of hard-rejected `(0.0, 'reject')`, allowing profitable low-win-rate
  strategies (e.g. high-premium outliers) to remain on the watchlist.
- **R4 — Skip-reason-by-gate enum** (`strategies/skip_reasons.py`) — `SkipGate` and `SkipReason`
  enums with a `REASON_TO_GATE` mapping. Two new columns (`skip_gate`, `skip_reason_code`) added
  to the `decisions` table. `TradeRecorder.record_decision` and `DecisionRepository.insert` accept
  the new fields. Key skip sites in `jobs/market_open.py` (liquidity floor, guardrail rejection,
  circuit breaker, Claude skip, no-candidates) now populate these columns.
- **R1 — Live-outcome feedback into recommender** — `TradeRepository` gains
  `get_closed_trades_for_symbol` and `get_closed_trades_in_window` methods. A module-level
  `trade_pnl` helper is defined in `database/repositories/trades.py`. `WatchlistRecommender`
  accepts an optional `trade_repo` and applies a live-performance penalty (−10/−15/−25 points)
  to incumbent symbols with poor recent live win rates, providing a feedback loop from live
  trading outcomes back into watchlist maintenance.
- **R2 — Recommendation accuracy tracking** — `recommendation_outcomes` table added to the schema
  (PK: `recommendation_id × outcome_type × window_days`). `OutcomeRepository` provides insert/
  exists/get_all. `OutcomeComputer` implements the 4-cell accuracy scorecard: accepted-add and
  rejected-remove use ground-truth live trades; rejected-add and accepted-remove use a proxy
  backtest over the same 90-day window. Phase 4 added to `jobs/weekly_research.py` to run the
  computation weekly. New API endpoint `GET /api/research/recommendations/outcomes` exposes
  all outcome rows.

## [1.4.0] - 2026-04-16

### Added
- **Conservative Wheel strategy** (`strategies/conservative_wheel_strategy.py`) — shorter-DTE variant
  of the wheel running in its own fourth Alpaca paper account. Key differences from the original Wheel:
  covered calls at 7–14 DTE (vs 21–35), CC strike above cost basis only (no Bollinger Band filter),
  5% max position size (vs 10%), 10 concurrent positions (vs 5), 3 rolls before closing (vs 2), and
  a wider roll delta window (short put within −0.40, short call within 0.45). All CSP entry rules,
  IV rank minimum, earnings avoidance, and universal guardrails are identical to the original Wheel.
- **Conservative Wheel prompts** — four state-specific prompt files
  (`prompts/conservative_wheel_*.md`) mirroring the original wheel prompt structure with the
  rule differences applied.
- **Conservative Wheel definition** (`strategies/definitions/conservative_wheel.json`) — encodes
  all entry, management, and guardrail parameters for the new strategy variant.
- **Conservative Wheel section on Strategies page** — plain-language explanation of what differs
  from the original Wheel and why, with SubCards for each rule change and a "What's the same"
  summary list.
- **Research layer — liquidity scoring** — `LiquidityRepository` scores each symbol/strategy pair
  by bid-ask spread, volume, and open interest; produces a multiplier (0.0–1.0) that gates new
  CSP and CC entries. Tier D (multiplier = 0.0) hard-blocks entry before calling Claude.
- **Research layer — win-rate gating** — `BacktestStatsRepository` looks up historical win rate
  per symbol/strategy; entries with win rate below a 30% floor are blocked before calling Claude.
  Both the liquidity and win-rate multipliers are attached to context for downstream logging.
- **Watchlist recommendation engine** (`research/recommendation_engine.py`) — weekly job scans
  the candidate universe, scores each symbol by liquidity tier, win rate, and regime fit, and
  produces ranked add/remove recommendations per watchlist.
- **Recommendations page** — dashboard UI displaying watchlist add/remove recommendations with
  scoring breakdown, sortable by score.
- **Weekly research job** (`jobs/weekly_research.py`) — runs `BacktestSweep` across the full
  candidate universe on a weekly schedule, refreshing `portfolio_patterns` and win-rate data.
- **BacktestSweep** — automated sweep that runs the backtesting engine across all symbols in the
  candidate universe and writes results to the backtest stats database.
- **S&P 500 bootstrap script** — one-shot script to seed the candidate universe with all S&P 500
  constituents; used to initialise the research layer.
- **Research Guide page** — Learn section page explaining the research layer, how liquidity tiers
  and win-rate scores are computed, and how they gate live entries.
- **Win Rate Heatmap** — Research page component showing win rate by symbol and regime as a
  colour-coded grid.
- **Claude API — prompt cache hit rate instrumentation** (Story 1) — `ClaudeAdvisor` records
  `cache_read_input_tokens` and `cache_creation_input_tokens` from each API response; cache hit
  rate surfaced in token usage reports.
- **Claude API — structured outputs** (Story 2) — `ClaudeAdvisor` requests JSON structured
  output; response schema validated against a Pydantic model before use; malformed responses
  trigger a structured retry rather than a crash.
- **Claude API — adaptive thinking + A/B harness** (Story 3) — `THINKING_MODE` env var enables
  `adaptive_medium` or `adaptive_high` extended thinking. A/B measurement harness records
  decision quality metrics (confidence delta, action distribution) split by thinking mode to
  quantify whether thinking tokens improve decisions.
- **Token usage tracking** — per-call and aggregate token usage (input, output, cache read,
  cache creation) recorded in SQLite; `/api/token-usage` endpoint exposes totals; dashboard
  renders weekly usage and estimated cost.
- **Counterfactual check for HOLD recommendations** — all position-management prompts
  (SHORT_PUT, LONG_STOCK, SHORT_CALL for both wheel variants and the spread strategies) now
  open with an explicit instruction: before recommending HOLD, ask whether the position would
  be opened today; if no, recommend CLOSE instead.
- **Guardrail rejection tracking** — when a guardrail fires and produces a SKIP decision, the
  `skip_reason` code is persisted to the decisions table; skip-reason distribution surfaced in
  the dashboard and API.
- **20-day high/low** added to `get_stock_technicals()` output — used in breakout and
  resistance checks in spread prompts.
- **Open interest caching** — option chain open interest values cached in SQLite to reduce
  redundant Alpaca calls on symbols scanned multiple times per cycle.
- **Claude's Playbook page** — Learn section page documenting the exact decision framework
  Claude follows for each strategy state, including prompt structure and guardrail order.

### Changed
- **Strategy router** now always includes both `"wheel"` and `"conservative_wheel"` in the
  active list; both wheels run their management cycles every tick and are blocked only in
  CRASH regime.
- **`config.py`** — `STRATEGY_ACCOUNT_MAP` extended with `"conservative_wheel"` entry pointing
  to `ALPACA_CONSERVATIVE_WHEEL_API_KEY/SECRET_KEY`; `CONSERVATIVE_WHEEL_MAX_POSITION_PCT`
  (0.05) and `CONSERVATIVE_WHEEL_MAX_CONCURRENT` (10) added as class constants;
  `_load_watchlist` loads a `"conservative_wheel"` key from `watchlist.json` with fallback
  to the `"wheel"` list.
- **`data/watchlist.json`** — `"conservative_wheel"` key added, initialised as a copy of the
  `"wheel"` list.
- **Strategies page Portfolio Risk section** — Conservative Wheel sizing caps added to the
  Position Sizing Caps SubCard; circuit breaker now described as monitoring "all four accounts".

## [1.3.1] - 2026-04-15

### Added
- **Screening Filters section** on each Account Detail page — collapsible read-only display
  of the entry criteria for each account's strategy, sourced from a static frontend data
  file (`frontend/src/data/screeningFilters.ts`). Covers Wheel (CSP + covered call),
  Iron Condor, and Spreads (bull put, bear call, long call vertical).

## [1.3.0] - 2026-04-15

### Added
- **Iron Butterfly strategy** (`strategies/iron_butterfly_strategy.py`) — sells ATM put + ATM
  call at the same center strike with OTM wings for protection. Defined-risk credit strategy
  with higher premium than iron condor but narrower profit zone. NEUTRAL + HIGH IV only.
  Assigned to Paper Account 4 (inactive until tested). Valid as single mleg order because
  wings cover both short legs.
- **Iron butterfly guardrails** — ATM short-strike validation ensures both short legs share the
  same center strike before order submission.
- **Calendar Spread strategy** (`strategies/calendar_spread_strategy.py`) — buys a longer-dated
  ATM option and sells a shorter-dated ATM option at the same strike. First vega-positive strategy
  in trade-pilot. NEUTRAL/BULL + LOW/MODERATE IV. Assigned to Paper Account 5 (inactive until
  tested).
- **Paper Accounts 4, 5, 6** added to account configuration with dedicated API keys.
- **Account configuration system** (`AccountManager`) — centralises per-account API key loading
  and strategy assignment; accounts are no longer hard-coded in job files.
- **Strategy definition JSON files** — each strategy now declares its entry rules, thresholds,
  and eligibility criteria in a JSON definition file; the strategy router and guardrails read
  from these files rather than hard-coded constants.
- **Account management API endpoints** — `/api/accounts` exposes account list and assignment;
  frontend renders accounts dynamically instead of from a hard-coded list.
- **Strategy name display** on `AccountCard` component — shows which strategy is assigned to
  each paper account.
- **Startup reconciler** — on service start, open trades with `PENDING_*` state are reconciled
  against Alpaca to recover from restarts that interrupted an in-flight order.
- **Fill quality and NTA events endpoints** — `/api/fill-quality` and `/api/nta-events` expose
  order fill analysis and near-the-ask event counts; both rendered in the frontend.
- **Option snapshot batching** — large symbol lists are split into batches before calling the
  Alpaca option snapshot endpoint to avoid request-size limits.
- **Strategy params injected into prompt templates** — `inject_strategy_params()` replaces
  placeholder tokens in Claude prompt files with live values before each API call, removing the
  need to hard-code thresholds inside prompt text.

### Changed
- **Strategy router** now reads eligibility criteria directly from strategy definition files
  rather than per-strategy conditional logic in the router module.
- **Guardrails** now read all numeric thresholds from strategy definition files.
- **Strategies page** and scheduler wired for Iron Butterfly (Paper Account 4) and Calendar
  Spread (Paper Account 5).
- **ORATS caches migrated from in-memory to SQLite** — cache survives service restarts,
  eliminating cold-start API storms after Render redeploys.

### Fixed
- Option snapshots now handle large symbol lists without hitting Alpaca payload limits.
- Short strangle entry corrected to submit two single-leg orders (Alpaca mleg rules reject two
  uncovered sell-to-open legs in a single order) — partial-fill risk was still unacceptable,
  which is why the strategy was ultimately replaced by Iron Butterfly.

### Removed
- **Short Strangle strategy** — replaced by Iron Butterfly. Alpaca's Level 3 mleg rules reject
  orders with two uncovered sell-to-open legs, making the strangle's entry non-atomic with
  unacceptable partial-fill risk.

## [1.2.0] - 2026-04-14

### Added

- **Backtest Intelligence page** — new dashboard page with three sections:
  environment match (compares current market regime to backtest conditions),
  similar trades (historical analogues for the proposed trade), and reality
  check (sanity checks between backtest assumptions and live conditions).
- **ORATS slippage model** — models realistic fill slippage using ORATS
  bid/ask spread data; unit-tested (`backtesting/slippage.py`).
- **ContextBuilder ORATS enrichment** — context builder now enriches spread
  candidates with ORATS snapshot data (IV rank, skew, term structure) and
  falls back to Alpaca market data when ORATS is unavailable.
- **Synthesized learn documents** — new knowledge-base entries covering
  implied volatility mechanics, non-directional strategies, and theta decay;
  raw notes files removed.

### Changed

- **SkipReasons and Strategies components** — updated with ORATS-sourced
  insights and expanded entry criteria display.
- **Backtest page** — added link to ORATS backtesting methodology in both
  the Backtest and Backtest Intelligence pages.
- **Alpaca account key naming** — environment variables renamed from
  strategy-tied names (`ALPACA_WHEEL_*`, `ALPACA_IRON_CONDOR_*`) to
  account-number names (`ALPACA_PAPER1_*`, `ALPACA_PAPER2_*`,
  `ALPACA_PAPER3_*`) to support flexible strategy-to-account assignment.

## [1.1.0] - 2026-04-13

### Added

- **Backtesting engine** — `BacktestEngine` simulates historical strategy
  performance day-by-day using ORATS historical data (`backtesting/engine.py`).
  Supports all five strategies with full regime routing, same entry/exit rules
  as live trading (50% profit close, 200% stop loss, DTE ≤ 7). Earnings-in-window
  detection tracks implied vs. realised earnings moves on positions held through
  an event.
- **ORATS historical data layer** — `ORATSHistorical` fetches and caches
  `/hist/summaries`, `/hist/ivrank`, and `/hist/strikes` in a local SQLite
  database (`data/orats_historical.py`). Eliminates redundant API calls on
  repeated backtest runs.
- **Backtester page** — interactive UI (`frontend/src/pages/Backtest.tsx`) with
  configurable params (strategy, symbols, date range, delta, DTE, IVR threshold,
  profit-close %, spread width), background job runner with live progress, equity
  curve, monthly returns bar chart, and full trade table with winner/loser filter.
- **ORATS expansion** — client now calls nine endpoints: `/summaries`,
  `/cores`, `/ivrank` (batch, up to 10 tickers), `/strikes`, `/monies/implied`,
  `/earnings`, `/hist/summaries`, `/hist/ivrank`, `/hist/strikes`.
  Per-endpoint TTLs: summaries/ivrank/monies 30 min, strikes 15 min,
  cores/earnings 6 hr.
- **Volatility surface** — `/monies/implied` provides model-smoothed IV at
  standardised delta levels (5–100) across all expirations, used for full-skew
  assessment in spread candidate scoring (`data/orats_client.py`).
- **EV scoring for spread candidates** — `build_spread_candidates()` in
  `context_builder.py` computes expected-value scores incorporating skew
  percentile adjustments. Put-selling strategies receive a premium when skew is
  elevated (put side overpriced vs. historical norm); call-selling strategies
  receive a discount.
- **Vol-of-vol classification** — ORATS `/cores` `vol_of_vol` field used to
  flag unstable IV environments. High vol-of-vol down-weights iron condor and
  theta strategies that require predictable conditions.
- **IV/HV ratio checks** — strategies compare current implied volatility to
  ORATS realised HV (`iv_hv_ratio` from `/cores`) before recommending premium
  sales. Ratio < 1.0 (IV cheaper than realised) suppresses put-selling signals.
- **Earnings volatility analysis** — context builder captures ORATS implied
  earnings move, historical average earnings move, and IV premium at entry.
  Engine and live bot record whether earnings fell inside a position's holding
  window and what the actual stock move was.
- **IV History chart** — `/api/iv-history` endpoint serves per-symbol
  historical IV rank from ORATS `/hist/ivrank`; Volatility dashboard renders
  it as a time-series line chart.
- **Source health tracking** — `data/source_health.py` records per-source
  success/failure counts and consecutive failures with atomic writes. Dashboard
  displays live coloured status dots; `DataSources` page shows last-success age
  per card.
- **Dashboard circuit breaker reset** — `/api/admin/reset-circuit-breaker`
  endpoint deletes `HALTED.lock` and the state file. Reset button appears in
  `TopBar` and on the `CircuitBreakers` page when status is RED or HALTED.
- **Per-strategy watchlists** — `WATCHLIST`, `IRON_CONDOR_WATCHLIST`, and
  `SPREAD_WATCHLIST` loaded from `data/watchlist.json`; editable from the
  dashboard Watchlist page without redeployment.
- **Watchlist page** — dashboard UI for viewing and editing per-strategy
  watchlists (`frontend/src/pages/Watchlist.tsx`).
- **Volatility dashboard page** — IV rank, term structure, skew, and IV
  history for any watchlist symbol (`frontend/src/pages/Volatility.tsx`).
- **How Backtesting Works** — new Learn page documenting the backtesting
  engine, parameter guide, result interpretation, benchmarks, and limitations
  (`frontend/src/pages/HowBacktestingWorks.tsx`).

### Changed

- **Sidebar** — collapsible section groups (Overview, Research, Accounts,
  Learn) with localStorage persistence and auto-expand when navigating directly
  to a Learn route. Learn defaults to collapsed. Sidebar is now scrollable with
  a thin themed scrollbar.
- **Research navigation group** — Backtester and Volatility moved out of
  Overview into a dedicated Research section.
- **TopBar logo** — logo and wordmark now link to the dashboard homepage.
- **CircuitBreakers page** — documents DRY_RUN bypass (always GREEN), stale
  peak guard (re-seeds peak when apparent drawdown > 50%), and both reset
  options (dashboard button and Render Shell file deletion).
- **Strategies page** — documents per-strategy watchlists (wheel, iron condor,
  spreads each have their own), full-watchlist scanning per cycle, and dashboard
  watchlist management.
- **DataSources page** — ORATS card expanded to cover all nine endpoints with
  per-endpoint cache TTLs; fallback chain updated; intro updated to "six
  external data providers."
- **HowItWorks page** — Step 3 updated to six data sources (adds CNN Fear &
  Greed); Step 6 describes full-watchlist pre-scanning for spread strategies.
- **About page** — data stack updated to include CNN Fear & Greed and full
  ORATS scope; Gather step updated to "six data sources."

## [1.0.0] - 2026-04-12

Initial public release.

### Added

- **Wheel strategy** — automated cash-secured-put / covered-call cycle
  (`strategies/wheel_strategy.py`) with per-symbol state machine
  (IDLE → SHORT_PUT → LONG_STOCK → SHORT_CALL).
- **Four defined-risk spread strategies** with per-strategy entry rules,
  Claude-driven decisions, and 50%-of-max-profit / DTE-based exit logic:
  - Iron condor (`strategies/iron_condor_strategy.py`)
  - Bull put credit spread (`strategies/bull_put_spread_strategy.py`)
  - Bear call credit spread (`strategies/bear_call_spread_strategy.py`)
  - Long call vertical debit spread (`strategies/long_call_vertical_strategy.py`)
- **Spread lifecycle reconciliation** — every spread tracks
  `IDLE → PENDING_OPEN → OPEN → PENDING_CLOSE → CLOSED`, with order-status
  reconciliation against Alpaca on `market_open` and after every entry/exit
  (`strategies/_spread_lifecycle.py`, `jobs/reconcile_orders.py`). Orders
  that never fill or are rejected automatically revert; management logic
  refuses to act on PENDING_* state.
- **Circuit breaker** with daily / weekly / drawdown loss limits that gate
  new entries: RED blocks all new entries, YELLOW blocks spread entries
  and requires high Claude confidence for wheel entries, GREEN is normal
  (`strategies/circuit_breaker.py`, gating in `jobs/market_open.py`). A
  `HALTED.lock` file can also be dropped to halt manually.
- **Guardrails** — pre-execution validators for every action: OCC symbol
  shape, qty / limit-price sanity, earnings proximity, ex-dividend
  proximity, IV regime, market regime, share ownership for covered calls,
  duplicate-position detection, and a shared-account capital cap across
  the three strategies that share the default Alpaca account
  (`strategies/guardrails.py`, gated at the `_handle_spread_open`
  chokepoint in `jobs/market_open.py`).
- **Strategy router** picks active strategies per cycle from market
  regime, IV environment, and circuit-breaker color
  (`strategies/strategy_router.py`).
- **Multi-account broker support** — wheel, iron condor, and the shared
  spread strategies can each run against their own Alpaca account
  (`brokers/alpaca_broker.py`, `brokers/broker_factory.py`).
- **Scheduled jobs** — `pre_market`, `market_open`, `position_check`,
  `pre_close`, `market_close`, `post_market`, `expiry_guard`,
  `portfolio_refresh`, `weekly_report`, `startup_snapshot`,
  `reconcile_orders`.
- **Persistence**:
  - SQLite dual-write (`database/db.py`, `database/recorder.py`,
    `database/repositories/`) with `cycles`, `decisions`, `trades`,
    `strategy_states`, `daily_summaries` tables. WAL mode for
    concurrent reader (API) and writer (scheduler) processes.
  - JSON snapshots under `data/snapshots/` for portfolio, context,
    circuit-breaker status, equity history, regime history, and
    per-strategy state.
  - Append-only JSONL trade journal (`data/trade_journal.py`) tagged
    with the running version of every entry.
- **FastAPI dashboard backend** (`api/server.py`) serving portfolio,
  decisions, performance, trades, equity history, regime history,
  circuit-breaker status, strategy states, and per-account portfolio
  snapshots.
- **Dashboard authentication** — mandatory `DASHBOARD_PASSWORD` env var,
  in-memory session store with 8-hour TTL, HttpOnly cookie + bearer
  token, inline login page, `noindex` enforcement on every HTML
  response, and tightened CORS (`allow_origins=[]`).
- **React + Vite frontend** (`frontend/`) with portfolio cards,
  decisions feed, performance + equity charts, trades view, strategy
  state inspector, circuit-breaker indicator, and version badge.
- **Public endpoints** — `GET /health` and `GET /api/health` (Render
  probes); `GET /changelog` (auth-gated, serves this file as plain text).
- **Claude integration** (`ai/claude_advisor.py`) — Claude Sonnet 4.6
  used for per-strategy entry/exit decisions with cached system prompt
  and per-strategy idle/open prompts under `prompts/`.
