# Phase 1 Spec — Per-Symbol Liquidity Scoring

**Status:** Draft for review
**Author:** Claude + operator
**Target release:** v1.1.0
**Scope:** Phase 1 of the three-phase Research layer. Phases 2 and 3 are out of scope for this doc.

---

## 1. Summary

Build a research subsystem that continuously collects option liquidity metrics for a curated candidate universe, computes a composite liquidity score per (symbol, strategy_type), and — once sufficient data exists — uses those scores to influence candidate ranking in `pre_check_entry`. The dashboard exposes the data read-only; nothing in guardrails changes.

This is **Version A** of Phase 1: research data influences live trading decisions as soon as scoring confidence is high enough. Safety rails are mandatory because of this.

---

## 2. Goals and non-goals

### Goals

- Generate a rolling liquidity score per (symbol, strategy_type) that reflects actual option trading conditions over a ~30-day window.
- Cover both the existing watchlist (via cycle-side collection) and a broader candidate universe (via weekly ORATS sweep).
- Integrate scores into `pre_check_entry` as a multiplicative factor on candidate ranking — without disturbing existing guardrails.
- Provide a new `/research` dashboard page showing scores, trends, and coverage.
- Maintain a kill switch to disable the live-integration path without redeploying.
- Log enough context (raw score, multiplier, final score, data confidence) to retrospectively answer "did this change anything?"

### Non-goals

- Changing guardrails. OI ≥ 200, bid-ask spread < 20%, earnings windows — all unchanged.
- Changing Claude prompts. The research layer operates entirely upstream of the Claude API call.
- Automatic watchlist modification. That's Phase 3.
- Historical backfill. We start collecting on day 1 of deployment; scores are "provisional" until 30 days of data exist.
- Per-contract liquidity time series. Aggregate at (symbol, strategy_type) only.
- Replacing the existing ad-hoc liquidity checks in guardrails. Research scoring is additive, not a replacement.

---

## 3. What gets computed

For each `(symbol, strategy_type)` pair in the candidate universe, Phase 1 computes and persists a **composite liquidity score** between 0 and 100.

### Sub-metrics (all measured at strategy-appropriate strikes/DTE)

| Metric | Definition | Weight |
|--------|------------|--------|
| `avg_ba_spread_pct` | Mean bid-ask spread as % of mid, across relevant contracts | 40% |
| `avg_oi_at_strikes` | Mean open interest at relevant strikes | 25% |
| `volume_to_oi_ratio` | Mean daily volume / OI — penalizes stale OI | 15% |
| `slippage_estimate` | Estimated cost of fill vs. mid (modeled per ORATS methodology: 56–75% of B/A width depending on # of legs) | 20% |

### Per-strategy strike and DTE ranges

These match each strategy's actual entry criteria. When we score AAPL for `bull_put_spread`, we only look at the contracts that strategy would ever trade — not AAPL's weekly ATM chain.

| strategy_type | Option type(s) | Delta range | DTE range |
|---------------|----------------|-------------|-----------|
| `wheel_csp` | Put | 0.20–0.30 | 21–35 |
| `wheel_cc` | Call | 0.20–0.35 | 21–35 |
| `bull_put_spread` | Put (short leg) | 0.20–0.30 | 21–35 |
| `bear_call_spread` | Call (short leg) | 0.20–0.30 | 21–35 |
| `iron_condor` | Both wings | 0.15–0.25 | 20–50 |
| `long_call_vertical` | Call (long leg) | 0.45–0.60 | 30–60 |

### Composite score formula

Each sub-metric is normalized to a 0–100 scale using a log-scaled percentile rank within the candidate universe (SPY is ~100 for most strategies; thinly-traded names land in single digits). Composite is a simple weighted sum.

```
composite = 0.40 * norm(avg_ba_spread_pct, inverse=True)
          + 0.25 * norm(avg_oi_at_strikes)
          + 0.15 * norm(volume_to_oi_ratio)
          + 0.20 * norm(slippage_estimate, inverse=True)
```

`inverse=True` means lower is better (e.g., tight spreads score high).

### Tiers

Composite scores are bucketed into tiers based on the **quartile distribution across the candidate universe** for that strategy:

- **Tier A** — top quartile. `pre_check_entry` multiplier: **1.20×**
- **Tier B** — second quartile. Multiplier: **1.00×** (neutral)
- **Tier C** — third quartile. Multiplier: **0.80×**
- **Tier D** — bottom quartile. **Hard floor — reject candidate regardless of raw score.**

Tier D is the only research-driven *rejection*. Tiers A–C only re-rank. Symbols without scoring data (new to universe, <30 days observations) default to Tier B (neutral) — they're neither rewarded nor penalized.

---

## 4. Data sources

Phase 1 collects data from **two complementary sources.** Both write to the same SQLite table; the `source` column distinguishes them.

### Source 1: Cycle-side collection (`research/liquidity/collector.py`)

Hook into `context_builder.py` so every live cycle emits a liquidity observation for the symbols it already fetched. No additional API calls — pure side-effect on existing data flow.

- **Coverage:** watchlist symbols only. ~30 symbols across the three watchlists.
- **Frequency:** every cycle the symbol is touched (~3–5× per day during market hours).
- **Granularity:** fine. Captures intra-week liquidity variation.
- **Cost:** zero (reuses data already fetched).
- **Limitation:** can't score candidates not in the watchlist.

### Source 2: Weekly ORATS scanner (`research/liquidity/scanner.py`)

New Sunday job pulls `get_strikes_on_date` from ORATS for each symbol × strategy_type in the candidate universe. Uses the ORATS historical API with today's date.

- **Coverage:** full candidate universe (~150 symbols after liquidity floor filtering).
- **Frequency:** once per week (Sunday 11:00 AM ET).
- **Granularity:** coarse. One snapshot per week per (symbol, strategy_type).
- **Cost:** ~150 symbols × 6 strategy variants = ~900 API calls per week. Well under ORATS rate limit (900/min) and daily budget.
- **Limitation:** stale between Sunday runs.

### Why both

The two sources cover different populations. Cycle-side keeps active watchlist data fresh between Sunday runs. The scanner extends coverage to symbols we're evaluating for potential addition to the watchlist. Together they give us: dense data for live trading, coarse data for candidate evaluation.

---

## 5. Candidate universe

### Initial composition

- All S&P 500 constituents (~500 symbols)
- 25 major ETFs: SPY, QQQ, IWM, DIA, XLE, XLF, XLK, XLV, XLY, XLI, XLP, XLU, XLRE, XLB, XLC, GLD, SLV, TLT, HYG, LQD, USO, UNG, FXI, EEM, EFA

Stored in `data/candidate_universe.json`:

```json
{
  "version": 1,
  "updated_at": "2026-04-20",
  "source": "sp500_plus_major_etfs",
  "symbols": ["AAPL", "MSFT", ...],
  "manual_adds": [],
  "manual_excludes": []
}
```

`manual_adds` and `manual_excludes` are operator-controlled. The scanner uses the union of `symbols + manual_adds` minus `manual_excludes`.

### Liquidity floor filter

Not every S&P 500 name has tradeable options (e.g., thin-option industrials). After each weekly scan, symbols that fall below minimum thresholds for *every* strategy type get flagged as `below_floor: true` in the scores table and stop being scanned weekly (one-shot re-check each quarter).

### Quarterly refresh

Operator task: regenerate `candidate_universe.json` from current S&P 500 list once per quarter. Phase 1 does not automate this — it's a 5-minute manual task and automating it risks silently including or excluding names without visibility.

---

## 6. Module layout

```
research/
├── __init__.py
├── README.md                              # Human-readable overview
├── candidates/
│   ├── __init__.py
│   └── universe.py                        # Load/maintain candidate_universe.json
└── liquidity/
    ├── __init__.py
    ├── collector.py                       # Cycle-side observation hook
    ├── scanner.py                         # Weekly ORATS-based sweep
    ├── scorer.py                          # Composite score computation
    ├── thresholds.py                      # Strategy-specific strike/DTE ranges, tier cutoffs
    └── repository.py                      # DB read/write for liquidity tables

jobs/
└── weekly_research.py                     # New Sunday 11:00 ET job

database/repositories/
└── liquidity_repository.py                # Injected into strategies for pre_check_entry reads

data/
├── candidate_universe.json                # Universe definition (operator-maintained)
└── snapshots/
    └── research_last_run.json             # When did weekly_research last succeed
```

### Frontend

```
frontend/src/pages/
└── Research.tsx                           # New page at /research

frontend/src/components/research/
├── LiquidityHeatmap.tsx                   # Rows=symbols, cols=strategies
├── SymbolDeepDive.tsx                     # Click-through for single symbol
└── CoverageStats.tsx                      # How many symbols have >30 days of data
```

---

## 7. SQLite schema

Two new tables. Both additive — no changes to existing tables.

```sql
CREATE TABLE IF NOT EXISTS symbol_liquidity_snapshots (
    snapshot_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol             TEXT NOT NULL,
    strategy_type      TEXT NOT NULL,
    snapshot_date      TEXT NOT NULL,
    source             TEXT NOT NULL,              -- 'cycle' or 'scanner'
    avg_ba_spread_pct  REAL,
    avg_oi_at_strikes  INTEGER,
    volume_to_oi_ratio REAL,
    slippage_estimate  REAL,
    sample_count       INTEGER,
    raw_metrics_json   TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE(symbol, strategy_type, snapshot_date, source)
);

CREATE INDEX IF NOT EXISTS idx_liq_snap_sym_strat
    ON symbol_liquidity_snapshots(symbol, strategy_type);
CREATE INDEX IF NOT EXISTS idx_liq_snap_date
    ON symbol_liquidity_snapshots(snapshot_date);

CREATE TABLE IF NOT EXISTS symbol_liquidity_scores (
    symbol            TEXT NOT NULL,
    strategy_type     TEXT NOT NULL,
    composite_score   REAL NOT NULL,               -- 0-100
    tier              TEXT NOT NULL,               -- 'A', 'B', 'C', 'D'
    lookback_days     INTEGER NOT NULL,
    snapshot_count    INTEGER NOT NULL,
    below_floor       INTEGER NOT NULL DEFAULT 0,  -- 0=no, 1=yes
    confidence        TEXT NOT NULL,               -- 'high' (>=30 snapshots), 'low' (<30), 'none'
    last_updated      TEXT NOT NULL,
    sub_metrics_json  TEXT,                        -- for dashboard display
    PRIMARY KEY (symbol, strategy_type)
);
```

The `scores` table is **materialized**, not a view — the weekly job rebuilds it from snapshots at the end of each run. Live code reads `scores`, never `snapshots`.

### Migration

Added to `database/db.py` `_MIGRATIONS` tuple as idempotent `CREATE TABLE IF NOT EXISTS` statements. Because they're `CREATE` not `ALTER`, they go in `_SCHEMA_STATEMENTS` instead of `_MIGRATIONS` — they're new tables, not additions to existing ones.

---

## 8. Integration with `pre_check_entry`

This is the single live-trading behavior change in Phase 1. Keep the change small and auditable.

### Current behavior (example: `bull_put_spread_strategy.py`)

```python
def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
    # ... guardrail-style checks ...
    best = context["spread_candidates"]["bull_put_spread"]["best_candidate"]
    credit_to_width = best["net_credit"] / best["wing_width"]
    return None, credit_to_width
```

### New behavior

```python
def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
    # ... existing checks unchanged ...
    raw_score = credit_to_width

    # Apply liquidity multiplier (Version A integration)
    symbol = context.get("symbol")
    multiplier, tier, confidence = self._liquidity_repo.get_multiplier(
        symbol, self.STRATEGY_TYPE,
    )

    # Tier D = hard floor, reject
    if tier == "D":
        return "below_liquidity_floor", 0.0

    final_score = raw_score * multiplier

    # Attach metadata for downstream logging
    context.setdefault("_research", {})["liquidity"] = {
        "raw_score": raw_score,
        "multiplier": multiplier,
        "tier": tier,
        "confidence": confidence,
        "final_score": final_score,
    }

    return None, final_score
```

### Kill switch

`config.py`:

```python
RESEARCH_SCORE_MULTIPLIER_ENABLED: bool = (
    os.getenv("RESEARCH_SCORE_MULTIPLIER_ENABLED", "true").lower() == "true"
)
```

When False, `liquidity_repo.get_multiplier` short-circuits and returns `(1.0, "B", "disabled")`. Raw score passes through unchanged. The `_research` metadata still gets attached with `"disabled"` confidence, so we know the flag was off.

### When multiplier activates

`get_multiplier` returns `(1.0, "B", "insufficient_data")` until a symbol has **≥30 snapshots** in its rolling window. This is the "sufficient data" threshold from the Version A decision. A new symbol added to the universe will take ~4 weeks of cycle-side collection (or ~30 weeks of scanner-only) to graduate to tiered scoring.

### Logging

Both raw and final scores get persisted in the `decisions` table via a new `research_metadata_json` column. Add this to `_MIGRATIONS`:

```python
"ALTER TABLE decisions ADD COLUMN research_metadata_json TEXT",
```

Dashboard can later display: "This decision picked HOOD over AMZN because liquidity Tier A (1.2×) boosted HOOD above AMZN's Tier B raw score."

---

## 9. Weekly research job

`jobs/weekly_research.py`. Runs Sunday 11:00 AM ET. Sequence:

1. **Scan.** For each (symbol, strategy_type) in candidate universe, call ORATS `get_strikes_on_date` with today's date + the strategy's delta/DTE filters. Compute sub-metrics. Insert into `symbol_liquidity_snapshots` with `source='scanner'`.
2. **Rescore.** For every (symbol, strategy_type) with at least one snapshot in the last 60 days: pull all snapshots from the last 30 days (both sources), compute fresh composite score, assign tier via universe-wide quartile cutoffs, update `symbol_liquidity_scores`.
3. **Floor check.** Any (symbol, strategy_type) that falls below minimum thresholds (e.g., avg OI < 50 across all recent snapshots) gets `below_floor=1`.
4. **Stats.** Write summary to `data/snapshots/research_last_run.json`: run duration, symbols scanned, symbols scored, coverage %, any errors.
5. **Alert.** If the job fails or coverage drops >10% WoW, log a warning (no Slack/email integration yet — just log-level).

### Resumability

The scanner processes one (symbol, strategy_type) per inner loop iteration. Each iteration either succeeds (insert snapshot) or fails (log + continue). Partial progress is preserved — there's no all-or-nothing transaction. A mid-run crash just means the remaining symbols are scanned next Sunday.

### Rate limit behavior

The existing ORATS client already rate-limits to 900/min. The scanner will hit this briefly on Sunday mornings. ~900 calls taking ~60 seconds total is fine; if it blocks for longer it just blocks — no failure path.

---

## 10. Frontend — `/research` page

### Initial scope (Phase 1)

Three components, stacked vertically:

1. **LiquidityHeatmap** — main visual. Rows: symbols (sorted by overall score). Columns: 6 strategy types. Cells colored by tier (A=green, B=yellow, C=orange, D=red, gray=no data). Hover shows composite score + confidence.

2. **CoverageStats** — small card showing:
   - Symbols in universe: X
   - Symbols with high-confidence scoring (≥30 snapshots): Y
   - Symbols with low-confidence scoring (<30 snapshots): Z
   - Symbols with no data yet: W
   - Last scanner run: timestamp + duration

3. **SymbolDeepDive** — click a row in the heatmap. Shows per-strategy breakdown for that symbol, last 4 weeks of composite score trend, and sub-metric contributions.

### Out of scope (Phase 3)

- "Add to watchlist" buttons
- Cross-strategy recommendations ("consider swapping MSFT wheel for HOOD")
- Any action buttons

### API endpoints

```
GET /api/research/liquidity/scores
  → full scores table, all symbols × strategies

GET /api/research/liquidity/symbol/:ticker
  → single-symbol deep dive: all strategy scores + snapshot history

GET /api/research/coverage
  → universe/coverage stats for the coverage card

GET /api/research/last-run
  → research_last_run.json contents
```

All auth-gated like the rest of the API.

---

## 11. Scheduler changes

In `scheduler.py`, add one line:

```python
schedule.every().sunday.at("11:00").do(lambda: safe_run(weekly_research.run, "weekly_research"))
```

11:00 AM ET is chosen to land between `weekly_report` cleanup (end of prior week) and any Sunday 6:00 PM ET report that might reference research output.

---

## 12. Configuration additions

New env vars, added to `config.py`:

```python
# Kill switch for research-driven pre_check_entry scoring.
RESEARCH_SCORE_MULTIPLIER_ENABLED: bool = (
    os.getenv("RESEARCH_SCORE_MULTIPLIER_ENABLED", "true").lower() == "true"
)

# Minimum snapshots before a symbol graduates from Tier B default.
RESEARCH_MIN_SNAPSHOTS_FOR_SCORING: int = int(
    os.getenv("RESEARCH_MIN_SNAPSHOTS_FOR_SCORING", "30")
)

# Lookback window (days) for rolling score calculation.
RESEARCH_LOOKBACK_DAYS: int = int(os.getenv("RESEARCH_LOOKBACK_DAYS", "30"))

# Whether to also score symbols flagged below_floor (default: skip them in weekly scan).
RESEARCH_SCAN_BELOW_FLOOR: bool = (
    os.getenv("RESEARCH_SCAN_BELOW_FLOOR", "false").lower() == "true"
)
```

Defaults are chosen so that a deployment with zero env changes behaves exactly as designed.

---

## 13. Testing strategy

### Unit tests

- `test_liquidity_scorer.py` — deterministic: given a list of mock contracts, composite score is X.
- `test_liquidity_repository.py` — read/write round-trip, kill switch behavior, tier computation.
- `test_candidate_universe.py` — add/exclude list merging.

### Integration tests

- `test_weekly_research_job.py` — mock ORATS client, run job end-to-end, assert rows inserted.
- `test_pre_check_integration.py` — inject mock liquidity scores, assert `pre_check_entry` returns expected final score. Include kill-switch-off test.

### Live validation (after first deploy)

Before the 4-week "sufficient data" threshold is crossed, the multiplier is effectively disabled for all symbols (all default to Tier B = 1.0×). This gives us a month to visually verify the dashboard, catch scoring bugs, and sanity-check tier assignments before live decisions are affected.

---

## 14. Rollback plan

If anything goes wrong after the multiplier activates:

1. **First response:** `RESEARCH_SCORE_MULTIPLIER_ENABLED=false` via env var, restart. Multiplier disabled, no redeploy needed. ~1 minute.
2. **If that fails:** remove the call to `liquidity_repo.get_multiplier` in `pre_check_entry`. Requires a deploy. ~10 minutes.
3. **Data is preserved** — snapshots and scores continue accumulating whether or not the multiplier is live. Re-enabling is a single env var flip.

The scanner and collector run regardless of the kill switch state — only the live integration is toggleable. This is intentional: if we disable integration after a month to debug, we don't lose the data accumulated during that debug window.

---

## 15. Success criteria

Phase 1 is successful when:

1. Weekly scanner runs reliably for 4 consecutive Sundays without operator intervention.
2. At least 100 (symbol, strategy) pairs reach "high confidence" scoring.
3. `/research` dashboard renders current scores and per-symbol deep dives correctly.
4. `decisions` table shows `research_metadata_json` populated for post-activation decisions.
5. No increase in scheduler crashes or circuit breaker trips attributable to the research layer.
6. Kill switch works — verified by toggling in staging.

Phase 1 is **not** successful if:

- Scanner errors consistently for specific symbols (ORATS coverage gaps we didn't anticipate).
- Score distributions are degenerate (everyone lands in one tier).
- Multiplier activation correlates with a measurable drop in decision quality after activation.

Phase 2 (historical win rates) is gated on Phase 1 passing all of the above.

---

## 16. Open questions for the operator

These are specific decisions I want the operator to make before code is written:

1. **Quartile cutoffs or absolute cutoffs for tiers?** I specified quartile (top 25% = A). Alternative: absolute cutoffs (composite ≥ 75 = A). Quartile is self-calibrating; absolute is more interpretable. I lean quartile but would accept either.

2. **Do we need a `DRY_RUN` mode for the weekly scanner?** I default to no — it's read-only against ORATS, writes to SQLite. Worst case of a bug is a bad score row, which the next run overwrites. But if you want a dry-run path that logs without writing, easy to add.

3. **Should symbols below the liquidity floor be shown on the dashboard at all?** I default to showing them grayed out so it's visible. Alternative: hide them entirely.

4. **Alert threshold for scanner failure?** I default to "log a warning." You might want Slack or email. Phase 1 can ship with just logging and add alerting later.

5. **Is `/api/research/liquidity/scores` returning the full matrix acceptable?** ~150 symbols × 6 strategies × ~10 fields = ~9000 JSON elements. A few hundred KB. Probably fine for one API call but we could paginate if it gets unwieldy.

---

## 17. Timeline estimate

Assuming operator approves this spec as-is:

| Week | Deliverable |
|------|-------------|
| 1 | SQLite schema, `research/candidates/universe.py`, `research/liquidity/thresholds.py`, `research/liquidity/scorer.py`, unit tests |
| 2 | `research/liquidity/collector.py` + `scanner.py`, `database/repositories/liquidity_repository.py`, `jobs/weekly_research.py`, integration tests |
| 3 | Kill switch, `pre_check_entry` integration across all 5 strategies, `decisions.research_metadata_json` migration, tests |
| 4 | Frontend `/research` page, API endpoints, CHANGELOG v1.1.0 entry, deploy to Render |

Total: ~4 weeks of evening work. Then ~4 weeks of data accumulation before the multiplier starts having effect. So **first real impact on live decisions is ~8 weeks from start of coding.**

---

## 18. What this spec does NOT address

Phase 1 only. The following are explicit Phase 2/3 concerns:

- Historical backtest win rates per (symbol, strategy, regime). → Phase 2
- Watchlist recommendation UI with Add/Reject buttons. → Phase 3
- Research-driven prompt updates. → Phase 3 (if ever)
- Automated candidate universe refresh from S&P 500 index feed. → Deferred
- Cross-strategy portfolio-level liquidity analysis. → Deferred

Write Phase 2 and 3 specs separately once Phase 1 is shipping.
