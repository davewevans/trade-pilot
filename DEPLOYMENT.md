# Deployment Guide

## Local Development

### First-time setup
```bash
# Clone the repo
git clone https://github.com/your-username/trade-pilot.git
cd trade-pilot

# Create virtual environment
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
copy .env.example .env
# Edit .env with your API keys
```

### Running locally

```bash
# Validate startup and connection
python main.py --job pre_market --dry-run

# Test a full market open cycle (no trades)
python main.py --job market_open --dry-run

# Test one symbol
python main.py --job market_open --symbol AAPL --dry-run

# Start the full scheduler (leave terminal open)
python main.py
```

### Testing the advisor
```bash
python scripts/test_advisor.py --symbol AAPL --phase idle
python scripts/test_advisor.py --symbol SPY --phase idle
```

### Checking enrichment data
```bash
python scripts/test_enrichment.py
```

---

## Render Deployment

### Step 1: Prepare the repo
- Ensure render.yaml is committed
- Ensure .env is in .gitignore (never commit API keys)
- Push to GitHub main branch

### Step 2: Create Render service
1. Go to render.com → New → Background Worker
2. Connect your GitHub repo (trade-pilot)
3. Render will detect render.yaml automatically
4. Click "Create Background Worker"

### Step 3: Set environment variables in Render dashboard
Go to your service → Environment → Add the following:

| Variable | Value | Notes |
|----------|-------|-------|
| ALPACA_PAPER1_API_KEY | your_key | Paper Account 1 key |
| ALPACA_PAPER1_SECRET_KEY | your_secret | Paper Account 1 secret |
| ALPACA_PAPER | true | Keep true until strategy is proven |
| ANTHROPIC_API_KEY | your_key | Claude API key |
| FRED_API_KEY | your_key | Risk-free rate |
| WATCHLIST | AAPL,SPY,MSFT | Comma-separated |
| DRY_RUN | true | Set to false when ready to trade |

### Step 4: First deploy
1. Render auto-deploys on git push
2. Go to Logs tab — confirm you see:
   "=== trade-pilot scheduler starting ==="
   "Startup validation passed"
3. Confirm no errors in the first 2 minutes

### Step 5: Monitor first market day
1. Check logs at 6:00 AM ET for pre-market job
2. Check logs at 9:30 AM ET for market open job
3. Review reports/daily/ via Render Shell tab:
   cat /data/reports/daily/$(date +%Y-%m-%d).md

### Step 6: Switch off dry run
Once you've confirmed the bot is making sensible decisions
for 1-2 weeks in dry-run mode:
1. Render dashboard → Environment
2. Change DRY_RUN from "true" to "false"
3. Redeploy (or it takes effect on next restart)

---

## Monitoring

### View today's report
In Render Shell:
```bash
cat /data/reports/daily/$(date +%Y-%m-%d).md
```

### View current positions
```bash
cat /data/reports/positions/current.md
```

### View recent logs
```bash
tail -100 /data/logs/scheduler.log
```

### View trade journal
```bash
tail -20 /data/journal.jsonl | python -m json.tool
```

---

## Rollback

If something goes wrong:
1. Render dashboard → your service → Deploys
2. Click on a previous successful deploy → "Rollback to this deploy"
3. Takes ~2 minutes

---

## Switching to Live Trading (Future)

When ready to move from paper to live:
1. Create a new Render service (do not modify the paper one)
2. Use live Alpaca API keys
3. Set ALPACA_PAPER=false
4. Start with DRY_RUN=true for one week
5. Reduce WATCHLIST to 1-2 symbols initially
6. Increase gradually as confidence builds
