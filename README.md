# trade-pilot

An automated options trading bot that uses the **Claude API** for decision-making and **Alpaca** for execution. Runs on paper trading accounts. Not financial advice — not ready for live money without further review.

---

## What It Is

trade-pilot runs a daily scheduled cycle that evaluates market conditions, asks Claude whether to open or manage options positions, and places limit orders through Alpaca. It supports multiple strategies across multiple Alpaca paper accounts, each strategy matched to a specific IV environment and market regime. A FastAPI server and React dashboard surface the bot's state in real time.

---

## Strategies

| Strategy | Account | IV Environment | Regime | Type |
|---|---|---|---|---|
| Wheel (CSP + CC) | `wheel` | Moderate+ (IVR ≥ 30) | BULL, NEUTRAL | Credit |
| Conservative Wheel | `conservative_wheel` | Moderate+ (IVR ≥ 30) | BULL, NEUTRAL | Credit |
| Iron Condor | `iron_condor` | High (IVR ≥ 50) | NEUTRAL | Credit |
| Bull Put Spread | `spreads` | Moderate+ (IVR ≥ 35) | BULL, NEUTRAL | Credit |
| Bear Call Spread | `spreads` | Moderate+ (IVR ≥ 35) | BEAR, NEUTRAL | Credit |
| Long Call Vertical | `spreads` | Low (IVR < 30) | BULL | Debit |
| Iron Butterfly | `iron_butterfly` | High (IVR ≥ 50) | NEUTRAL | Credit |
| Calendar Spread | `calendar_spread` | Low/Moderate | NEUTRAL, BULL | Debit |

The `spreads` account uses a `StrategyRouter` to select among bull put, bear call, and long call vertical based on the current confirmed regime and IV environment. At most one spread opens per cycle; management checks always run.

Market regime (BULL / NEUTRAL / BEAR / CRASH / EUPHORIA) is determined from VIX, SPX trend, and the Fear & Greed index during the pre-market job and confirmed over 3 consecutive readings before the bot acts on a change.

---

## Architecture

```
scheduler.py  ──────────────────────────────────────────────  runs jobs on a schedule
    │
    ├── jobs/pre_market.py         FRED macro, regime classification
    ├── jobs/market_open.py        entry evaluation — calls Claude, places orders
    ├── jobs/position_check.py     position management — roll, close, or hold
    ├── jobs/expiry_guard.py       safety sweep for same-day expiries
    ├── jobs/pre_close.py          warnings for positions within 7 DTE
    ├── jobs/market_close.py       end-of-day reconciliation
    ├── jobs/post_market.py        cleanup, NTA events, fill quality
    ├── jobs/portfolio_refresh.py  every-5-minutes dashboard refresh
    └── jobs/weekly_report.py      Sunday performance summary

ai/claude_advisor.py              wraps Claude API — builds prompt, parses decision
strategies/circuit_breaker.py     YELLOW/RED halts on drawdown or daily loss
strategies/guardrails.py          pre-trade validation rules (position size, IV, delta)
strategies/strategy_router.py     selects which spread strategy runs each cycle
data/context_builder.py           assembles market context for Claude
data/spread_tracker.py            tracks open spread positions in SQLite
database/                         SQLite schema, repositories, recorder
api/server.py                     FastAPI — serves snapshots to the dashboard
frontend/                         React + Tailwind dashboard
```

**Job schedule** is defined in `config.SCHEDULE` — see `GET /api/schedule` for the authoritative list. Do not restate times in docs.

---

## Project Structure

```
trade-pilot/
├── ai/              Claude advisor and prompt assembly
├── api/             FastAPI server (server.py, run.py)
├── backtesting/     Backtest engine
├── brokers/         Alpaca broker wrapper (base.py, alpaca_broker.py)
├── data/            Context builder, spread tracker, state writer, snapshots
├── database/        SQLite schema, repositories, trade recorder
├── docs/            Architecture and workflow docs
├── frontend/        React + Vite + Tailwind dashboard
├── jobs/            Scheduled job modules
├── notifications/   ntfy.sh / email alert backends
├── prompts/         Claude system prompts (system.md)
├── reporting/       Daily/weekly report generation
├── research/        Backtest sweep and watchlist recommendation engine
├── strategies/      Strategy implementations, circuit breaker, guardrails, router
├── utils/           Retry, market hours, OCC symbol parsing, file I/O
├── config.py        Settings (env vars) and SCHEDULE constant
├── main.py          CLI entry point
├── scheduler.py     Job registration (iterates config.SCHEDULE)
└── version.py       VERSION and VERSION_DATE
```

---

## Tech Stack

| | |
|---|---|
| Language | Python 3.14 |
| AI | Anthropic Claude (`claude-sonnet-4-6` default) |
| Broker | Alpaca (paper trading, `alpaca-py`) |
| Scheduler | `schedule` library — ET timezone throughout |
| API server | FastAPI + Uvicorn |
| Database | SQLite (`database/db.py`, path: `data/trade_pilot.db`) |
| Dashboard | React 18, React Router 6, Recharts, Tailwind CSS 3, Vite 5 |
| Data sources | ORATS (IV rank, skew), FRED (macro), Finnhub (earnings) |
| Deployment | Render.com — Background Worker (scheduler) + Web Service (API + frontend) |

---

## Running Locally

### 1. Install dependencies

```bash
pip install -r requirements.txt
cd frontend && npm ci && npm run build
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in the required values (see [Environment Variables](#environment-variables) below).

### 3. Start the scheduler

```bash
py main.py                              # start the scheduler
py main.py --dry-run                    # run without placing orders
py main.py --job pre_market             # run a single job and exit
py main.py --job market_open --dry-run  # test the entry cycle
py main.py --job market_open --symbol AAPL  # run against one symbol
```

### 4. Start the API server (separate terminal)

```bash
py api/run.py              # serves at http://localhost:8000
py api/run.py --port 3001  # custom port
```

The React dashboard is served by the same FastAPI process at `http://localhost:8000`. Log in with the password set in `DASHBOARD_PASSWORD`.

---

## Environment Variables

### Required

| Variable | Description |
|---|---|
| `ALPACA_PAPER1_API_KEY` | Alpaca paper account 1 API key (spreads account) |
| `ALPACA_PAPER1_SECRET_KEY` | Alpaca paper account 1 secret |
| `ANTHROPIC_API_KEY` | Claude API key |
| `FRED_API_KEY` | Federal Reserve FRED API key (macro data) |
| `DASHBOARD_PASSWORD` | Password for the web dashboard login |

### Account credentials (per strategy)

Each strategy maps to a dedicated Alpaca paper account. Set the key/secret pair for any account you want active:

| Variable pair | Account |
|---|---|
| `ALPACA_PAPER2_API_KEY` / `_SECRET_KEY` | Wheel account |
| `ALPACA_PAPER3_API_KEY` / `_SECRET_KEY` | Iron Condor account |
| `ALPACA_PAPER4_API_KEY` / `_SECRET_KEY` | Iron Butterfly account |
| `ALPACA_PAPER5_API_KEY` / `_SECRET_KEY` | Calendar Spread account |
| `ALPACA_CONSERVATIVE_WHEEL_API_KEY` / `_SECRET_KEY` | Conservative Wheel account |

`ALPACA_PAPER1` is the spreads account (bull put, bear call, long call vertical).

### Optional

| Variable | Default | Description |
|---|---|---|
| `ALPACA_PAPER` | `true` | Use paper trading endpoint |
| `ORATS_API_KEY` | `""` | ORATS IV rank and skew data |
| `FINNHUB_API_KEY` | `""` | Finnhub earnings calendar |
| `DRY_RUN` | `false` | Log decisions without placing orders |
| `NTFY_TOPIC` | `""` | ntfy.sh topic for push alerts (empty = disabled) |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy server URL |
| `ALERT_FILLS` | `true` | Send push alerts on order fills |
| `PROMPT_CACHE_TTL` | `5m` | Claude prompt cache TTL |
| `THINKING_MODE` | `off` | Claude extended thinking (`off` / `adaptive_medium` / `adaptive_high`) |
| `DATABASE_PATH` | `data/trade_pilot.db` | SQLite database path |
| `DAILY_LOSS_HALT_PCT` | `3.0` | Daily loss % that triggers RED halt |
| `DAILY_LOSS_REDUCE_PCT` | `1.5` | Daily loss % that triggers YELLOW halt |
| `WEEKLY_LOSS_HALT_PCT` | `5.0` | Weekly loss % that triggers RED halt |
| `DRAWDOWN_HALT_PCT` | `10.0` | Drawdown % that triggers RED halt |
| `DRAWDOWN_LOCK_PCT` | `15.0` | Drawdown % that writes permanent HALTED.lock |
| `RENDER` | `false` | Set to `true` on Render (changes `DATA_DIR` to `/data`) |

---

## Deployment (Render.com)

`render.yaml` defines a single **Web Service** that runs both the API server and the frontend (FastAPI serves the built React SPA as a static catch-all). The scheduler runs as a separate **Background Worker** service.

Persistent disk (`/data`, 1 GB) stores the SQLite database, trade journal, logs, and snapshot files. The disk is required — without it, state is lost on redeploy.

Set all required env vars (API keys, `DASHBOARD_PASSWORD`) in the Render dashboard environment tab — never commit them to the repo or `render.yaml`.

Python version is pinned to 3.14 in `render.yaml`.

---

## Safety Features

**Circuit breaker** (`strategies/circuit_breaker.py`) — Monitors aggregate portfolio equity across all accounts each cycle. Triggers YELLOW (reduce) or RED (halt) on daily loss, weekly loss, or total drawdown thresholds. A RED halt writes `data/HALTED.lock` which survives restarts. Manually delete the lock file to resume trading.

**Guardrails** (`strategies/guardrails.py`) — Validates every Claude decision before execution: position size, delta range, IV rank, earnings proximity, sector concentration, existing position conflicts. Rejections are logged and surfaced in the dashboard.

**HALTED.lock kill switch** — Create `data/HALTED.lock` (any content) to immediately halt all trading without stopping the scheduler process.

**Dry-run mode** — Set `DRY_RUN=true` or pass `--dry-run` to run the full decision cycle (Claude is called, decisions are logged) without placing any orders.

---

## Status

v1.5.0 — paper trading only. No real capital is at risk. This project is for personal development and educational purposes. It is not financial advice and is not suitable for live trading without independent review, testing, and risk assessment.
