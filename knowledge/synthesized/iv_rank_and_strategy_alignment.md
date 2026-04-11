# Implied Volatility: Rank, Percentile, and Strategy Alignment

**Source:** Charles Schwab Education — "Using Implied Volatility Percentages and Rankings" + "Aligning Options Strategies and Implied Volatility"
**Synthesized for:** trade-pilot / wheel strategy
**Save to:** `knowledge/synthesized/iv_rank_and_strategy_alignment.md`

---

## 1. Summary

Implied volatility (IV) is the market's forward-looking estimate of how much a stock will move, expressed as an annualized percentage. IV Rank (IVR) and IV Percentile place the current IV reading inside its 52-week range, answering the question: "Is IV high or low *right now* for this specific stock?" This context is the single most important factor in determining whether to sell or buy options premium — and it directly governs trade-pilot's entry filter.

---

## 2. Key Concepts

### What IV Tells You
- IV is calculated from current options prices using the Black-Scholes-Merton model
- Expressed as an annualized percentage: a 30% IV means the market expects a ~30% price range (up or down) over the next year
- The 1-standard-deviation expected range covers ~68% of outcomes
- **IV does not predict direction — only magnitude**
- IV includes a risk premium above what HV alone would suggest, which is why selling premium tends to be statistically favorable over time

### IV Rank (IVR) vs. IV Percentile
These terms are often used interchangeably but are calculated differently:

| Metric | Formula | What It Answers |
|--------|---------|----------------|
| **IV Rank (IVR)** | `(Current IV - 52wk Low) / (52wk High - 52wk Low) × 100` | Where current IV sits in its full 52-week range |
| **IV Percentile** | % of days in past year where IV was *below* current IV | What fraction of days had lower IV than today |

**Example:** If IV ranged from 15% to 45% over 52 weeks and current IV is 30%:
- IVR = (30-15)/(45-15) × 100 = **50**
- IV Percentile = depends on how many days IV was below 30% (could be different from 50)

⚠️ **The bot's `iv_rank >= 30` filter uses IVR (range-based), not percentile.** These can diverge significantly when IV had a brief spike that inflated the 52-week high. A stock with IVR of 30 might have IV Percentile of 70 if that spike was an outlier. This is a known limitation.

### IV vs. Historical Volatility (HV)
- HV measures *realized* past price movement (backward-looking)
- IV is *implied* by current options prices (forward-looking)
- **IV > HV → options are relatively expensive → favorable environment for selling premium**
- **IV < HV → options are relatively cheap → favors buying strategies**
- The gap between IV and HV is called the "volatility risk premium" — the long-run statistical edge of selling options

### IV Mean Reversion
- IV tends to be mean-reverting: extreme readings typically revert toward historical averages
- High IV often signals that options sellers are being compensated for elevated uncertainty — and when that uncertainty resolves, IV collapses (beneficial for short premium positions)
- Low IV that stays low for extended periods can trap premium sellers in thin-margin trades

### Strategy Selection by IV Level

| IV Environment | Favored Strategies | Avoid |
|---------------|-------------------|-------|
| **High IV (IVR > 50)** | Sell CSPs, covered calls, credit spreads | Buying calls/puts (overpaying for premium) |
| **Moderate IV (IVR 30–50)** | Sell CSPs/CCs with tighter targets | Defined-risk spreads with limited credit |
| **Low IV (IVR < 30)** | Long calls/puts, debit spreads, calendar spreads | Selling premium (collecting too little) |

---

## 3. Actionable Insights for trade-pilot

### Entry Filter Validation
The current `iv_rank >= 30` threshold is reasonable but sits at the moderate boundary. At IVR exactly 30:
- Premium collected will be thin relative to risk taken
- A rising IV environment after entry can hurt short vega positions before theta kicks in
- Consider treating IVR 30–40 as a "yellow zone" where Claude should require stronger technical confirmation before entering

### IV vs. HV Comparison as a Confidence Signal
Currently, `context_builder.py` fetches IV rank but does **not** explicitly compare IV to HV. Adding the IV/HV ratio as a context field would let Claude know whether options are objectively expensive (IV > HV) or just elevated relative to recent history (high IVR). This is a meaningful distinction.

**Suggested addition to context package:**
```python
"iv_vs_hv_ratio": round(current_iv / current_hv_30d, 2)  # > 1.0 means IV expensive
```

### IV Direction Matters, Not Just Level
A falling IV after entry on a short premium position is ideal (positive vega position + falling vega = profit). A rising IV after entry hurts the position even if theta is working. Claude should have awareness of whether IV appears to be rising or falling at time of entry.

**Suggested context field:**
```python
"iv_trend_5d": "rising" | "falling" | "stable"  # based on 5-day IV change direction
```

---

## 4. Contradictions / Gaps

⚠️ **IVR vs. IV Percentile ambiguity:** The articles use "IV Rank" and "IV Percentile" somewhat interchangeably, but they can produce very different numbers for the same stock. The bot should document which calculation `context_builder.py` actually uses and make sure Claude's prompt reflects that.

⚠️ **IVR of 30 as threshold is not validated:** The sources confirm that high IV favors selling premium and low IV does not. They do not establish 30 as a specific threshold. This number likely comes from practitioner convention (tastytrade community standard). It is reasonable but arbitrary.

⚠️ **IV risk premium persists but is not guaranteed:** The articles correctly note that IV > HV does not guarantee options are mispriced — markets are forward-looking and IV may be "correct" about future vol being higher than HV. The edge in selling premium is statistical over many trades, not guaranteed on any individual trade.

---

## 5. Prompt Impact

### `prompts/system.md` — Add under "Volatility Analysis" section:
```
## Volatility Context

**IV Rank (IVR):** Where current IV sits in the 52-week range (0-100).
- IVR > 50: premium is elevated — strong environment for selling CSPs/CCs
- IVR 30-50: premium is moderate — acceptable with strong technical confirmation
- IVR < 30: premium is thin — skip; do not sell options in low-IV environments

**IV vs HV Ratio:** When provided, prefer iv_vs_hv_ratio > 1.0 for new entries.
A ratio above 1.0 confirms that implied volatility is higher than realized volatility —
the core statistical edge of premium selling.

**IV Trend:** A falling IV trend at time of entry is ideal for short premium positions.
A rising IV trend at entry means vega will work against the position initially;
require higher IVR (>= 40) before entering in a rising IV environment.
```

### `prompts/wheel_idle.md` — Add:
```
When evaluating IV for CSP entry:
- IVR >= 40: favorable — proceed if other criteria met
- IVR 30-39: borderline — require stock above 20-day SMA AND RSI < 60 before entering
- IVR < 30: SKIP — premium collection insufficient for the risk taken
```

---

## 6. Synthesized Document

*This file is the synthesized document. Save as `knowledge/synthesized/iv_rank_and_strategy_alignment.md`*
