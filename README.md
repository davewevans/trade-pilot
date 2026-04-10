# 🤖 trade-pilot

An algorithmic trading bot that uses **Alpaca** for paper trading (strategy development & testing) and **Charles Schwab** for live trading. Built with Python.

---

## Overview

`trade-pilot` is a personal algorithmic trading framework designed with a clear separation between **strategy logic** and **broker execution**. The broker layer is swappable, meaning the same strategy code runs against Alpaca's paper trading environment during development and Schwab's live brokerage when ready for production.

```
┌─────────────────────────────────┐
│         Strategy Logic          │
└────────────────┬────────────────┘
                 │
         ┌───────▼────────┐
         │  Broker Layer  │
         └───────┬────────┘
        ┌────────┴────────┐
        ▼                 ▼
   [Alpaca]           [Schwab]
  Paper Trading      Live Trading
```

---

## Features

- 📈 **Dual-broker support** — Alpaca (paper) and Charles Schwab (live)
- 🔁 **Pluggable strategy architecture** — swap strategies without changing broker code
- 📊 **Market data streaming** — real-time price feeds via WebSocket
- 🧪 **Paper trading first** — validate strategies risk-free before going live
- 📉 **Backtesting support** — test strategies against historical data
- 🔐 **Secure credential management** — environment variables, no secrets in code

---

## Tech Stack

- **Language**: Python 3.11+
- **Alpaca SDK**: [`alpaca-py`](https://github.com/alpacahq/alpaca-py)
- **Schwab SDK**: [`schwabdev`](https://github.com/tylerebowers/Schwabdev)
- **Backtesting**: `vectorbt` or `backtrader` (TBD)
- **Environment**: `python-dotenv`

---

## Project Structure

```
trade-pilot/
├── brokers/
│   ├── base.py           # Abstract broker interface
│   ├── alpaca_broker.py  # Alpaca implementation
│   └── schwab_broker.py  # Schwab implementation
├── strategies/
│   └── base.py           # Abstract strategy interface
├── data/
│   └── market_data.py    # Market data fetching & streaming
├── backtesting/
│   └── runner.py         # Backtesting engine
├── config.py             # Configuration & broker selection
├── main.py               # Entry point
├── .env.example          # Environment variable template
├── requirements.txt
└── README.md
```

---

## Getting Started

### 1. Clone the repo

```bash
git clone https://github.com/<your-username>/trade-pilot.git
cd trade-pilot
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Set up environment variables

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Broker selection: "alpaca" or "schwab"
BROKER=alpaca

# Alpaca (paper trading)
ALPACA_API_KEY=your_api_key
ALPACA_SECRET_KEY=your_secret_key
ALPACA_PAPER=true

# Schwab (live trading)
SCHWAB_APP_KEY=your_app_key
SCHWAB_APP_SECRET=your_app_secret
```

### 4. Run the bot

```bash
python main.py
```

---

## Broker Setup

### Alpaca (Paper Trading)

1. Create a free account at [alpaca.markets](https://alpaca.markets)
2. Navigate to **Paper Trading** in the dashboard
3. Generate API keys under **Your API Keys**
4. Set `ALPACA_PAPER=true` in your `.env`

### Charles Schwab (Live Trading)

1. Register at [developer.schwab.com](https://developer.schwab.com)
2. Create a new app to receive your App Key and App Secret
3. Set your callback URL to `https://127.0.0.1`
4. On first run, complete the OAuth browser flow — tokens are automatically managed thereafter

---

## Development Workflow

```
1. Backtest          →  Test strategy on historical data
2. Paper Trade       →  Run live on Alpaca with fake money
3. Monitor & Tune    →  Track performance, refine parameters
4. Go Live           →  Switch BROKER=schwab and deploy
```

---

## Disclaimer

This project is for **educational and personal use only**. Algorithmic trading involves significant financial risk. Past performance does not guarantee future results. Always understand the risks before trading with real money.

---

## License

MIT
