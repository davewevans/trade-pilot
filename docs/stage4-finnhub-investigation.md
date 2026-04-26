# Stage 4 Finnhub Tier + Caller Dependency Investigation

**Date:** 2026-04-26  
**Author:** investigation script + grep analysis  
**Status:** COMPLETE — decision pending implementation

---

## 1. Empirical Free-Tier Results

Script: `scripts/investigate_finnhub_tier.py`  
Symbols tested: AAPL, TSLA, SPY, QQQ, XLE, GLD (+ sector ETF check: XLF, XLK)

### `/stock/profile2`

| Symbol | HTTP | Data returned |
|--------|------|---------------|
| AAPL   | 200  | Full: `finnhubIndustry="Technology"`, `marketCapitalization=3979469.6`, `name`, `country`, `exchange`, `currency`, `ipo`, `shareOutstanding` |
| TSLA   | 200  | Full: `finnhubIndustry="Automobiles"`, marketCap present |
| SPY    | 200  | **Empty `{}`** |
| QQQ    | 200  | **Empty `{}`** |
| XLE    | 200  | **Empty `{}`** |
| GLD    | 200  | **Empty `{}`** |
| XLF    | 200  | **Empty `{}`** |
| XLK    | 200  | **Empty `{}`** |

**Finding:** `/stock/profile2` is structurally empty for ETFs — not a tier limitation. No 403 errors.

### `/stock/metric`

| Symbol | HTTP | Field count | PE  | Div yield | 52W High/Low |
|--------|------|------------|-----|-----------|--------------|
| AAPL   | 200  | 128–132    | ✓   | 0.0038    | ✓            |
| TSLA   | 200  | 128–132    | ✓   | None      | ✓            |
| SPY    | 200  | 19 (sparse)| None| None      | ✓            |
| QQQ    | 200  | 19 (sparse)| None| None      | ✓            |
| XLE    | 200  | 19 (sparse)| None| None      | ✓            |
| GLD    | 200  | 19 (sparse)| None| None      | ✓            |

**Finding:** Free tier confirmed — equities get full 128+ field suite; ETFs get a sparse 19-field set (price/return metrics only). `52WeekHigh` and `52WeekLow` present for **all** symbols. `peBasicExclExtraTTM` and `dividendYieldIndicatedAnnual` present for equities, `None` for ETFs.

---

## 2. Caller Dependency Results

### 2a. `pe_ratio`

- `data/market_data.py:649` — populated from `info.get("trailingPE")`
- `tests/integration/test_context_builder_truth_audit.py:53` — test fixture
- `scripts/test_advisor.py:68` — manual test fixture
- `frontend/src/pages/DataSources.tsx:271` — dashboard label only
- **Prompts/strategies: ZERO hard reads** — Claude sees it as context but no guardrail or skip gate reads `pe_ratio`
- **Classification: context enrichment only (informational)**

### 2b. `market_cap`

- `data/market_data.py:650` — populated from `info.get("marketCap")`
- Tests and fixtures only
- **Prompts/strategies: ZERO hard reads**
- **Classification: context enrichment only (informational)**

### 2c. `sector`

- `data/market_data.py:651` — populated from `info.get("sector")`
- `prompts/system.md:525` — `"in the same sector. Check fundamentals.sector"` (Claude reasoning, not a guardrail)
- `strategies/guardrails.py:943` — **sector cap check**: `"sector" in r and any(w in r for w in ("max", "positions"))` — this reads from a rejection-reason *string*, not from the fundamentals dict; no direct dependency on `fundamentals["sector"]`
- **Classification: Claude prompt reasoning + portfolio concentration narrative — no hard guardrail dependency**

### 2d. `industry`

- `data/market_data.py:652` — populated from `info.get("industry")`
- Tests and fixtures only; no prompt or strategy references
- **Classification: vestigial / unused in live paths**

### 2e. `annual_dividend_yield`

- `data/market_data.py:358` — returned by `get_ex_dividend_date()` (yfinance path); **None in Alpaca path (Stage 3 regression)**
- `data/context_builder.py:377` — `context["ex_dividend"]["annual_dividend_yield"]` ← this is what prompts read
- `prompts/wheel_short_call.md:31` — `ex_dividend.annual_dividend_yield > 1%` (Claude's dividend-aware call decision)
- `prompts/turnover_wheel_short_call.md:32,34` — same pattern; uses yield to estimate next dividend
- `prompts/turnover_wheel_long_stock.md:49` — same pattern
- `strategies/guardrails.py:350` — `ex_div.get("annual_dividend_yield")` — soft warn only (log-only, not a block)
- **Classification: Claude prompt reasoning + soft guardrail warn — `None` is currently safe but degrades wheel yield analysis**

### 2f. `fifty_two_week_high` / `fifty_two_week_low`

- `data/market_data.py:654,655` — populated from yfinance
- `tests/integration/test_context_builder_truth_audit.py:58,59` — as `week_52_high`, `week_52_low`
- `scripts/test_advisor.py:73,74` — manual fixture
- **Prompts/strategies: ZERO hard reads; passed to Claude as context**
- **Classification: context enrichment only — but Finnhub has this for ALL symbols including ETFs**

### 2g. `days_to_earnings` / `ex_dividend_data_available`

These are the two fields with **hard safety consequences**:

- `strategies/guardrails.py:452,562,641,720,802,876` — **six validators** all read `fund.get("days_to_earnings")` as a hard block
- `strategies/guardrails.py:732` — **bear call spread** reads `fund.get("ex_dividend_data_available")` — rejects defensively if `False`
- **Classification: safety-critical — must not regress**

**Current state:** `days_to_earnings` in `get_fundamentals()` uses `ticker.calendar` (yfinance). `get_earnings_calendar()` in `market_data.py` already uses Finnhub as primary with yfinance fallback. But `get_fundamentals()` is NOT calling `get_earnings_calendar()` — it has its own duplicated yfinance calendar logic.

---

## 3. Cross-Reference Table

| Field | Current source | Finnhub free tier | ETF coverage | Caller impact |
|-------|---------------|-------------------|--------------|---------------|
| `next_earnings_date` | yfinance `ticker.calendar` | `/stock/earnings-calendar` (already via `get_earnings_calendar()`) | ETF = None (expected) | Hard guardrail via `days_to_earnings` |
| `days_to_earnings` | derived | same | ETF = None (expected) | Hard guardrail in 6 validators |
| `pe_ratio` | yfinance `trailingPE` | `/stock/metric.peBasicExclExtraTTM` ✓ equity | ETF = None | Claude context only |
| `market_cap` | yfinance `marketCap` | `/stock/profile2.marketCapitalization` ✓ equity | ETF = None | Claude context only |
| `sector` | yfinance `sector` | `/stock/profile2.finnhubIndustry` ✓ equity | **ETF = `{}` → needs hardcoded map** | Claude prompt reasoning |
| `industry` | yfinance `industry` | Not available (only broad `finnhubIndustry`) | None | Unused in live paths |
| `avg_volume` | yfinance `averageVolume` | Not available on free tier | N/A | Claude context only; `get_stock_technicals()` already has `avg_volume_10d/30d` |
| `fifty_two_week_high` | yfinance `fiftyTwoWeekHigh` | `/stock/metric.52WeekHigh` ✓ ALL symbols | **ETF = ✓** | Claude context only |
| `fifty_two_week_low` | yfinance `fiftyTwoWeekLow` | `/stock/metric.52WeekLow` ✓ ALL symbols | **ETF = ✓** | Claude context only |
| `analyst_rating` | yfinance `recommendations` | `/stock/recommendation` (already `get_finnhub_analyst_data()`) | ETF = None | Claude context only |
| `annual_dividend_yield` | yfinance `dividendYield` (via `get_ex_dividend_date`) | `/stock/metric.dividendYieldIndicatedAnnual` ✓ equity | ETF = None | Wheel prompt reasoning + soft guardrail warn |
| `ex_dividend_data_available` | Set `True/False` by `get_fundamentals()` | Must set `True/False` in new Finnhub implementation | N/A | **Hard bear call spread guardrail** |

---

## 4. Sector ETF Behavior

All ETFs tested return `{}` from `/stock/profile2`. This is structural — Finnhub's company profile endpoint simply doesn't cover funds. The six active trading symbols include SPY and QQQ (index ETFs) and the wheel universe includes non-ETF equities like NVDA, AAPL, TSLA.

**Required ETF sector map for Stage 4** (hardcoded, since no data source provides it):

```python
_ETF_SECTOR_MAP = {
    "SPY": "ETF/Index",
    "QQQ": "ETF/Index",
    "IWM": "ETF/Index",
    "DIA": "ETF/Index",
    "GLD": "ETF/Commodity",
    "SLV": "ETF/Commodity",
    "USO": "ETF/Commodity",
    "XLE": "ETF/Energy",
    "XLF": "ETF/Financial",
    "XLK": "ETF/Technology",
    "XLV": "ETF/Healthcare",
    "XLI": "ETF/Industrial",
    "XLY": "ETF/Consumer Discretionary",
    "XLP": "ETF/Consumer Staples",
    "XLU": "ETF/Utilities",
    "XLB": "ETF/Materials",
    "XLRE": "ETF/Real Estate",
}
```

`industry` field for ETFs: return `None`. Finnhub doesn't provide fund sub-classifications and the field has no callers.

---

## 5. Architecture Note: Earnings Duplication

`get_fundamentals()` currently contains its own yfinance calendar parsing (lines 606–621) that **duplicates** `get_earnings_calendar()` logic. Stage 4 should eliminate this duplication: `get_fundamentals()` should call `get_earnings_calendar(symbol)` for the `next_earnings_date` / `days_to_earnings` fields instead of running its own calendar fetch. This unifies earnings data under the Finnhub → yfinance fallback path already established in Stage 3.

---

## Recommendation: Stage 4 Contract

### What to build

Replace `get_fundamentals()` to use Finnhub as primary, yfinance as fallback:

**Primary (Finnhub):**
1. Call `get_earnings_calendar(symbol)` — already Finnhub-primary (reuse, don't duplicate)
2. Call Finnhub `/stock/profile2` — get `finnhubIndustry` (→ `sector`) and `marketCapitalization` (→ `market_cap`)
3. Call Finnhub `/stock/metric?metric=all` — get `peBasicExclExtraTTM` (→ `pe_ratio`), `52WeekHigh/Low`, `dividendYieldIndicatedAnnual` (→ `annual_dividend_yield`)
4. Call `get_finnhub_analyst_data(symbol)` — already exists, reuse for `analyst_rating`
5. Apply ETF sector map override when `/stock/profile2` returns `{}`
6. Set `ex_dividend_data_available: True` on success; `False` on hard error (preserves guardrail contract)

**Fallback (yfinance):**
- Same structure as today — if Finnhub path raises, fall back to `yf.Ticker(symbol).info`
- `ex_dividend_data_available: False` only if BOTH Finnhub AND yfinance fail

**Fields dropped (no callers):**
- `avg_volume` — replace with `None`; technicals already provide `avg_volume_10d/30d` separately
- `industry` — can keep as `None` for ETFs, copy `finnhubIndustry` for equities (no callers rely on it)

### Kill switch

Add `USE_FINNHUB_FOR_FUNDAMENTALS: bool` env var (default `True`) following the established pattern in `config.py`.

### Annual dividend yield restoration

In Stage 4, `get_fundamentals()` should also return `annual_dividend_yield` from Finnhub metric. Context builder should populate `context["ex_dividend"]["annual_dividend_yield"]` from `fundamentals.get("annual_dividend_yield")` when the Alpaca path returns `None` for yield — this restores the wheel prompt reasoning without a separate API call.

Alternatively (simpler): just patch `get_ex_dividend_date()` to additionally call Finnhub metric for yield when `USE_ALPACA_FOR_EX_DIVIDEND=True`. Decision deferred to implementation.

### Safety invariants that must not regress

1. `fund.get("ex_dividend_data_available")` must be `False` (not `None`) when data fetch fails — bear call spread rejects defensively
2. `fund.get("days_to_earnings")` must be populated via `get_earnings_calendar()` which already has Finnhub → yfinance fallback
3. Bear call spread defensive patch (`context["fundamentals"]["ex_dividend_data_available"]`) must remain in place until Stage 4 ships and is confirmed in production

### ETF behavior summary (post-Stage 4)

| Symbol type | `sector` | `pe_ratio` | `market_cap` | `fifty_two_week_*` | `annual_dividend_yield` |
|-------------|---------|-----------|-------------|--------------------|-----------------------|
| Equity (AAPL, TSLA) | Finnhub `finnhubIndustry` | Finnhub metric | Finnhub profile | Finnhub metric | Finnhub metric |
| ETF (SPY, QQQ, GLD) | **Hardcoded map** | None | None | **Finnhub metric ✓** | None (ETF doesn't report yield via metric) |

### Scope boundary

Stage 4 is `get_fundamentals()` only. `get_earnings_calendar()` and `get_ex_dividend_date()` are already migrated (Stage 3). The remaining yfinance usages after Stage 4 will be:
- `get_stock_technicals()` — OHLCV history via `yf.download()`; Stage 6 will migrate to Alpaca bars
- `get_earnings_date()` — legacy wrapper, appears to have no live callers (check before Stage 5)
- Backtester `yf.download()` calls — Stage 6
