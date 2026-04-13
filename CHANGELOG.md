# Changelog

All notable changes to trade-pilot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
