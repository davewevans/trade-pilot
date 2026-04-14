# Changelog

All notable changes to trade-pilot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
