# Directional Options Strategies: Strategy Profiles for trade-pilot

**Source:** Charles Schwab Course — "Directional Options: Single Options and Spreads"
**Covers:** Long Calls/Puts, Long Vertical Spreads, Short Vertical Spreads
**Synthesized for:** trade-pilot multi-strategy expansion
**Save to:** `knowledge/synthesized/directional_strategies.md`

---

## 1. Summary

This course documents four directional strategies: long calls, long puts, long vertical spreads (debit), and short vertical spreads (credit). Unlike the non-directional strategies synthesized previously, these trades require a directional thesis — bullish or bearish — on the underlying. The short put vertical is the most immediately relevant to trade-pilot as a defined-risk alternative to the naked CSP. The short call vertical is a bearish income strategy with no existing analog in the bot. Long calls and puts are speculative vehicles best deployed when a strong catalyst or technical setup exists. All four belong in the multi-strategy expansion.

---

## 2. The Directional Framework

Unlike non-directional strategies where time and IV are the primary profit drivers, **price movement is the primary driver here**. However, time and IV still matter — significantly:

- **Long options** (calls/puts, long verticals): vega positive, theta negative — need the move to happen quickly; hurt by IV crush and time decay
- **Short verticals** (credit spreads): vega negative, theta positive — profit from time passing and/or IV falling; don't need a big move, just need price to stay on the right side of the short strike

This creates a clean pairing with the shared IV regime context from the non-directional synthesis:

| IV Environment | Directional Strategy |
|---|---|
| **Low IV** | Buy long calls/puts or long verticals — options are cheap, want IV to rise |
| **High IV** | Sell short verticals — collect elevated premium, want IV to fall |

---

## 3. Strategy Profiles

---

### Strategy A: Long Call (Directional Bullish)

**What it is:** Buy a call option. Profit if the underlying rises above the break-even before expiration.

**Greek profile:**
| Greek | Direction | Impact |
|-------|-----------|--------|
| Delta | Positive | Gains value as stock rises |
| Gamma | Positive | Delta accelerates on moves higher |
| Theta | **Negative** | Loses value every day — the enemy |
| Vega | Positive | Benefits from rising IV; hurt by IV crush |

**When to use:**
- Bullish directional thesis with a specific catalyst or technical setup
- Low IV environment (options are cheaper; IV expansion benefits the position)
- Uptrending underlying — stock above 50-day SMA making higher highs/lows
- Avoid: earnings within the trade window (IV crush destroys the position even if the stock moves right)

**Entry parameters (from course):**
- Underlying: uptrending, avg daily volume ≥ 1M shares, no earnings within DTE
- Entry trigger: support bounce (CAHOLD pattern — Close Above High Of Low Day)
- Expiration: **50–100 DTE** (balances theta cost vs. premium; longer than the expected 5–15 day move)
- Strike: **ATM or slightly ITM** — balance between cost and probability of profit
  - Deep OTM: cheap but requires large move; lower probability
  - Deep ITM: expensive, wide spreads; closely tracks stock but capital-intensive
- Bid/ask spread: ≤ 10% of ask price
- Position size: risk **0.5–1% of portfolio** per trade; contracts = portfolio_risk / premium_paid

**Max profit:** Theoretically unlimited (call) / large but capped at zero (put)
**Max loss:** Premium paid (100% of investment)
**Break-even:** Strike + premium paid

**Management rules (exit hierarchy):**
1. **Stop loss:** Close if option value drops 50% from purchase price
2. **Entry signal invalidated:** Close if stock falls back below the low of the entry candle
3. **Pace check:** If the underlying hasn't moved 50% of expected distance in 50% of the forecasted time → close; the move isn't happening
4. **Time stop:** Close at **20 DTE** regardless — gamma risk and theta acceleration make holding past this point poor risk/reward
5. **Profit target:** Close when option doubles (100% gain) or underlying reaches price target
   - Price target calculation: (resistance − support) + resistance = projected move

**Long put:** Mirror image — bearish, same entry/management logic reversed. Note: IV often spikes when stocks fall → vega works *in favor* of long puts (unlike long calls where IV typically falls as stock rises).

**Automation difficulty:** Medium-high. Requires technical analysis signal detection (support bounce, CAHOLD pattern), pace tracking, and multiple layered exit rules. The "is the move happening on schedule" check is particularly difficult to automate cleanly.

**⚠️ Key risk:** You must be right on direction, magnitude, AND timing. Three-dimensional correctness requirement means high failure rate even when the directional thesis is correct. The 0.5–1% position size reflects this.

---

### Strategy B: Long Call Vertical Spread (Directional Debit)

**What it is:** Buy a lower-strike call + sell a higher-strike call, same expiration. Net debit. Bullish. Reduces cost and theta drag of a naked long call by selling a further OTM call against it.

**Long put vertical:** Bearish mirror — buy higher-strike put + sell lower-strike put. Same mechanics.

**Greek profile (position-dependent):**
| Greek | Below midpoint | Above midpoint |
|-------|---------------|----------------|
| Delta | Positive | Positive |
| Gamma | Positive | Negative |
| Theta | **Negative** | **Positive** |
| Vega | Positive | Negative |

Key insight: theta and vega *flip sign* depending on whether the underlying is below or above the midpoint between the two strikes. Once the underlying is in the profit zone (above the midpoint for a call vertical), theta starts working *for* the trade.

**When to use:**
- Bullish thesis but want to reduce premium cost vs. naked long call
- Low IV environment preferred (long option is the driver; cheap options benefit buyers)
- Less capital-efficient than a naked long call but lower absolute risk

**Entry parameters (from course):**
- Underlying: bullish chart pattern — sideways to uptrending, support bounce entry trigger
- Expiration: **20–50 DTE** (shorter than naked long call — expecting a near-term move)
- Long strike: **first ITM strike** below current price (intrinsic value drives profit)
- Short strike: **OTM strike** at or near price target (where you expect the stock to reach)
- Bid/ask: ≤ 10% of ask on the spread
- Position size: risk **1–2% of portfolio** per trade

**Max profit:** (Short strike − Long strike) − net debit. Fixed. Achieved when underlying is above the short strike at expiration.
**Max loss:** Net debit paid. Achieved when underlying is below the long strike at expiration.
**Break-even:** Long strike + net debit

**Management rules:**
- If spread is ITM (both options above strikes): close **4–10 DTE** to avoid assignment risk; or close at **75–80% of max profit** — whichever comes first
- If spread is OTM (both below lower strike): little to do — let expire worthless (transaction fees often exceed buyback value of worthless long call)
- If long call ITM, short call OTM (between strikes): close both legs; close short first or simultaneously to avoid naked call exposure
- Position size: 1–2% portfolio risk

**vs. Long call:** Long vertical gives up unlimited upside for lower cost and lower risk. The short call partially offsets theta drag — making this more forgiving in sideways-trending markets than a naked long call.

**Automation difficulty:** Medium. Simpler than a naked long call because management is primarily "hold until profit target or expiration" rather than tracking technical price targets. The primary decision is just: is it ITM or OTM as expiration approaches?

---

### Strategy C: Short Call Vertical Spread (Bearish Income)

**What it is:** Sell a lower-strike OTM call + buy a higher-strike OTM call, same expiration. Net credit. Bearish to neutral. Profit if underlying stays below the short call strike at expiration.

**Greek profile (position-dependent):**
| Greek | Below midpoint | Above midpoint |
|-------|---------------|----------------|
| Delta | Negative | Negative |
| Gamma | Negative | Positive |
| Theta | **Positive** | **Negative** |
| Vega | **Negative** | Positive |

Below midpoint (profitable zone): theta positive, vega negative — time and falling IV both help.
Above midpoint (losing zone): theta negative, vega positive — same Greek flip as seen in long verticals.

**When to use:**
- Bearish to neutral thesis — expecting the underlying to decline or stay flat
- High IV environment preferred (selling elevated premium; want IV to fall)
- Downtrending or range-bound underlying; ideally entry near resistance level
- Avoid: bullish or strongly trending-up underlyings

**Entry parameters (from course):**
- Underlying: downtrending or sideways; pullback from resistance as entry trigger
- Expiration: **20–50 DTE**
- Short strike: OTM call with **low delta** (lower delta = higher probability of expiring worthless, lower premium)
- Long strike: further OTM call above the short strike (caps max loss; cheaper = higher net credit but wider max loss)
- Bid/ask: ≤ 10% of ask
- Position size: risk calculated as (wing width − credit received); keep to **1–2% of portfolio** max loss per trade

**Max profit:** Net credit received. Achieved when underlying stays below short strike at expiration.
**Max loss:** (Short strike − Long strike) − net credit. Fixed. Achieved when underlying is above both strikes at expiration.
**Break-even:** Short strike + net credit

**Management rules:**
- If spread is OTM: close when **short call bid ≤ $0.05** — most time value is extracted, remaining risk not worth holding
- If short call ITM, long call OTM: close both sides if long call still has value; close short only if long is worthless (let long ride as cheap lottery on a reversal)
- If spread fully ITM: close both sides several days before expiration to avoid assignment chaos; or let go to expiration and accept max loss (long call caps it)
- ⚠️ If assigned on short call before expiration: results in short stock position — immediately close using the long call or buy back stock

**vs. Naked short call:** Adding the long call costs premium but defines max loss (no unlimited downside). Makes the strategy suitable for automation; naked short calls are not.

**vs. Wheel's covered call:** Short call vertical is more capital-efficient (no need to own 100 shares) but has no shares to deliver if assigned — the long call acts as the hedge instead. Better fit for bearish setups where you don't want stock ownership.

**Automation difficulty:** Low-medium. Cleanest management of the four directional strategies. The primary decision at any point is: is the short strike ITM or OTM? Management rules for each scenario are concrete and binary.

---

### Strategy D: Short Put Vertical Spread (Bullish Income — Key Strategy)

**What it is:** Sell a higher-strike OTM put + buy a lower-strike OTM put, same expiration. Net credit. Bullish to neutral. Profit if underlying stays above the short put strike at expiration.

**This is the defined-risk version of a cash-secured put.** It is almost certainly the highest-priority new strategy for trade-pilot because it directly addresses the wheel's most significant weakness: naked downside risk on the CSP.

**Greek profile:** Mirror image of short call vertical — delta positive, theta positive in profitable zone, vega negative in profitable zone.

**When to use:**
- Bullish to neutral thesis — expecting underlying to stay flat or rise
- High IV preferred (selling elevated premium)
- Same entry logic as a CSP but with defined max loss
- No capital requirement to secure the full put (no "cash-secured" requirement — long put acts as the hedge)

**Entry parameters (from course):**
- Underlying: same as CSP — bullish setup, above support, ideally above 50-day SMA
- Expiration: **20–50 DTE**
- Short strike: OTM put with **delta 0.20–0.30** (same as current CSP target)
- Long strike: further OTM put below short strike — typically 5–10 points lower; closer = less risk, costs more; further = more risk, lower cost
- Position size: risk = (wing width − credit); **1–2% of portfolio** max loss per trade

**Max profit:** Net credit received. Achieved when underlying stays above short put strike at expiration.
**Max loss:** (Short strike − Long strike) − net credit. Fixed. Achieved when underlying drops below the long put strike.
**Break-even:** Short strike − net credit

**Management rules:**
- Identical logic to short call vertical, reversed for puts
- Close when short put bid ≤ $0.05 (most premium extracted, risk not worth carrying)
- Close when 75–85% of max profit captured
- Close 4–10 DTE if short put is ITM to avoid assignment
- ⚠️ If assigned on short put: results in long stock position — close stock immediately or keep if within wheel parameters

---

## 4. Strategy Comparison Matrix

| | Long Call/Put | Long Vertical | Short Call Vertical | Short Put Vertical |
|--|--|--|--|--|
| **Bias** | Bull/Bear | Bull/Bear | Bearish/Neutral | Bullish/Neutral |
| **Credit/Debit** | Debit | Debit | **Credit** | **Credit** |
| **IV Regime** | Low IV | Low IV | **High IV** | **High IV** |
| **Delta** | Positive/Negative | Positive/Negative | Negative | Positive |
| **Theta** | **Negative** | Mixed | **Positive** (OTM zone) | **Positive** (OTM zone) |
| **Vega** | Positive | Mixed | **Negative** (OTM zone) | **Negative** (OTM zone) |
| **Max loss** | Premium paid | Net debit | Wing − credit | Wing − credit |
| **Max gain** | Unlimited/large | Wing − debit (fixed) | Net credit (fixed) | Net credit (fixed) |
| **Probability** | Lower | Moderate | Moderate-High | Moderate-High |
| **DTE target** | 50–100 | 20–50 | 20–50 | 20–50 |
| **Automation** | Hard | Medium | **Easy** | **Easy** |
| **Position size** | 0.5–1% | 1–2% | 1–2% | 1–2% |

---

## 5. The Short Put Vertical as a CSP Upgrade

This deserves special attention. The current wheel bot sells naked CSPs. The short put vertical is strictly superior for automation for these reasons:

**Defined max loss:** A naked CSP on a $50 stock with $50 strike has a theoretical max loss of $4,850 ($5,000 cost basis minus any premium). A $50/$45 short put vertical on the same stock has a max loss of ~$400 regardless of how far the stock falls. This changes the risk/reward profile fundamentally.

**No capital requirement for full position:** A naked CSP on a $50 stock requires $5,000 in buying power. A $50/$45 short put vertical requires only the margin on the spread width (~$500 max loss). This frees up capital for other strategies.

**Same entry logic:** Short strike delta 0.20–0.30, 20–50 DTE, high IV preferred, avoid earnings. These are nearly identical to the current CSP rules.

**The trade-off:** You give up assignment-to-shares upside (the wheel's core feature). If assigned on the short put, the long put doesn't deliver shares — it just caps the loss. You're out of the trade, not into a covered call setup.

**Recommendation:** Consider running the short put vertical as a separate strategy (its own Alpaca account, its own state machine) rather than replacing the CSP in the wheel. The wheel stays intact for stocks you're willing to own. The short put vertical is for high-IV income trades on stocks where you don't want assignment.

---

## 6. Updated IV Regime Map (All Strategies)

Combining both courses:

| IV Rank | Regime | Favored Strategies |
|---------|--------|-------------------|
| < 30 | Low IV | Long calls/puts, Long verticals, Calendar spreads, Diagonal spreads |
| 30–50 | Moderate | Wheel (CSP/CC), Short put verticals |
| > 50 | High IV | Iron condors, Butterflies, Short call verticals, Short put verticals |

This is the shared context layer's core routing signal.

---

## 7. Concrete Entry Parameter Summary

### Long Call / Long Put
- DTE: 50–100
- Strike: ATM or first ITM
- Entry trigger: support bounce (technical signal required)
- IV: Low (IVR < 30)
- Position risk: 0.5–1% of portfolio
- Earnings: must be outside the trade window

### Long Call/Put Vertical
- DTE: 20–50
- Long strike: first ITM
- Short strike: at expected price target (OTM)
- Entry trigger: technical support bounce
- IV: Low (IVR < 30)
- Position risk: 1–2% of portfolio

### Short Call Vertical
- DTE: 20–50
- Short strike: OTM call, low delta (0.20–0.30)
- Long strike: further OTM, same expiration
- Underlying: downtrending or near resistance
- IV: High (IVR > 40)
- Position risk: 1–2% of portfolio (based on max loss = wing − credit)

### Short Put Vertical
- DTE: 20–50
- Short strike: OTM put, delta 0.20–0.30
- Long strike: further OTM put, same expiration
- Underlying: bullish or neutral, above support
- IV: High (IVR > 40)
- Position risk: 1–2% of portfolio (based on max loss = wing − credit)

---

## 8. Contradictions / Gaps

⚠️ **Long calls/puts are the hardest to automate.** Three-dimensional correctness (direction + magnitude + timing) plus technical signal detection makes this genuinely difficult. The "pace check" exit (50% of move in 50% of time) requires tracking a price target established at entry. This is possible but adds context state complexity.

⚠️ **The course recommends 50–100 DTE for long calls** — significantly longer than the 21–35 DTE used for the wheel. This is counter-intuitive but logical: the longer DTE reduces theta drag while you wait for the technical setup to play out. The bot would need a separate DTE target for long directional strategies vs. short premium strategies.

⚠️ **Short put vertical vs. CSP assignment flow:** The wheel relies on CSP assignment to transition to LONG_STOCK. The short put vertical breaks this cycle — assignment on the short put results in long stock, but the long put hedges the downside rather than transitioning to a covered call. If you add short put verticals, the state machine needs to handle this differently from a naked CSP assignment.

⚠️ **The "close when short bid ≤ $0.05" rule for short verticals** is a novel exit rule not currently in the wheel prompts. It's more mechanical than the "50% of credit" rule the wheel uses and is worth considering as an addition to the short vertical strategy prompts — and potentially backporting to the wheel's covered call management.

⚠️ **Short call vertical requires a bearish thesis** — the bot currently has no bearish strategies. To run short call verticals, the shared context layer needs a `market_regime` signal that can identify bearish setups (downtrending stock + near resistance), and Claude needs technical analysis context (SMA direction, resistance levels) to form that thesis.

---

## 9. Recommended Rollout Priority

| Strategy | Priority | Reason |
|----------|----------|--------|
| **Short Put Vertical** | High — next after iron condor | Nearly identical to CSP; defined risk; clean automation |
| **Short Call Vertical** | Medium | Bearish income; new direction for the bot; requires bearish thesis |
| **Long Call/Put Vertical** | Medium | Lower automation complexity than single-leg; useful in low-IV regimes |
| **Long Call/Long Put** | Low | Hardest to automate; highest failure rate; smallest position size |

---

## 10. Synthesized Document

*This file is the synthesized document. Save as `knowledge/synthesized/directional_strategies.md`*
