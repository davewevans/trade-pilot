# Non-Directional Options Strategies: Strategy Profiles for trade-pilot

**Source:** Charles Schwab Course — "Non-Directional Options: Advanced Spreads"
**Covers:** Calendar Spreads, Diagonal Spreads, Iron Condors, Long Butterfly Spreads
**Synthesized for:** trade-pilot multi-strategy expansion
**Save to:** `knowledge/synthesized/non_directional_strategies.md`

---

## 1. Summary

This course documents four non-directional strategies that profit from time decay and IV changes rather than directional price movement. Each strategy has a distinct IV regime preference, Greek profile, and management approach. Together they form a coherent expansion menu for trade-pilot beyond the wheel — covering both high-IV environments (iron condors, butterflies) and low-IV environments (calendars, diagonals). This document captures the concrete entry parameters, management rules, and automation considerations for each.

---

## 2. The IV Regime Map — Critical Framework

Before anything else, the single most important insight from this course:

**Strategy selection must match the IV environment.** This is the organizing principle:

| IV Environment | Favored Strategies | Why |
|---|---|---|
| **Low IV / IV likely to rise** | Calendar spreads, Diagonal spreads | Vega positive — profits from IV expansion |
| **High IV / IV likely to fall** | Iron condors, Butterfly spreads | Vega negative — profits from IV contraction |

This creates a natural pairing with the shared macro context layer:
- **IV Rank low (< 30):** Deploy calendars or diagonals
- **IV Rank high (> 50):** Deploy iron condors or butterflies
- **IV Rank moderate (30–50):** Either works; other factors determine selection

The bot's shared context layer should feed IV Rank to every strategy's decision engine as the primary strategy-selection signal.

---

## 3. Strategy Profiles

---

### Strategy A: Long Calendar Spread

**What it is:** Buy a longer-dated ATM option, sell a shorter-dated ATM option at the same strike. Both same type (put or call).

**Greek profile:**
| Greek | Direction | Meaning |
|-------|-----------|---------|
| Delta | ~Neutral (ATM) | Doesn't depend on price direction |
| Gamma | Negative | Hurt by large price moves either way |
| Theta | **Positive** | Gains value as time passes |
| Vega | **Positive** | Gains value as IV rises |

**When to use:**
- Overall market neutral, IV low (bottom 50% of 52-week range)
- Underlying expected to stay sideways near the strike
- Large-cap, low-volatility underlying preferred (less likely to gap)

**Entry parameters (from course):**
- Long option: **60–120 DTE**
- Short option: **20–40 DTE**
- Strike: **ATM** for neutral bias; OTM for bullish/bearish tilt
- Underlying: avg daily volume ≥ 1M shares; options bid/ask spread ≤ 10% of ask
- IV Rank: **< 50** (ideally bottom 50% of 52-week range)
- Avoid earnings within the expiration window

**Max profit:** When underlying is exactly at strike at short option expiration. Rises with IV.
**Max loss:** Net debit paid. Occurs on large moves in either direction.
**Break-even:** Two points; vary with IV — cannot be precisely calculated in advance.

**Management rules:**
- Roll short option when: theta approaches zero (< 10 DTE on short), OR short option reaches 75–85% of max profit
- If short option ITM: roll to OTM strike (converts to diagonal), or close entire spread
- If IV spikes: consider closing partial position to capture unrealized gain
- Close entire spread: 4–10 days before long option expiration
- Position size: risk 1–2% of portfolio per trade; contracts = portfolio_risk / debit_paid

**Automation difficulty:** Medium. Two expirations require tracking separately. Rolling the short option multiple times is the core management action. Max profit/loss is not static — changes with IV.

**⚠️ Key risk:** Short option can be assigned at any time. Assignment converts spread to a single long option with a completely different risk profile.

---

### Strategy B: Long Diagonal Spread

**What it is:** Buy a longer-dated ITM option, sell a shorter-dated OTM option at a different strike. Same type (put or call). Directionally biased version of a calendar.

- **Long call diagonal** = bullish bias (buy ITM call far expiry, sell OTM call near expiry)
- **Long put diagonal** = bearish bias (buy ITM put far expiry, sell OTM put near expiry)

**Greek profile:**
| Greek | Direction | Meaning |
|-------|-----------|---------|
| Delta | Negative (put diagonal) / Positive (call diagonal) | Has directional bias |
| Gamma | Negative | Hurt by large adverse moves |
| Theta | **Positive** | Time works in favor |
| Vega | **Positive** | Benefits from IV expansion |

**When to use:**
- Low IV environment (bottom 50% of 52-week range)
- Neutral to mildly directional outlook on underlying
- Underlying you know well; large-cap preferred
- Long put diagonal: works well ahead of earnings (bearish + rising IV post-event)

**Entry parameters (from course):**
- Short option: **20–40 DTE**, delta **–0.30 to –0.40** (puts) / 0.30–0.40 (calls)
- Long option: **60–120 DTE**, delta **–0.60 to –0.70** (puts) / 0.60–0.70 (calls) — deeper ITM
- IV Rank: **< 50** — want IV to expand from here
- Underlying: high daily volume; bid/ask spread ≤ 10%; sideways or mildly directional
- Avoid earnings within the window

**Max profit:** When short option is ATM at expiration. Rises with IV.
**Max loss:** Net debit (long premium minus short credit). Only occurs on moves in the wrong direction (unlike calendar, which loses on moves either way).
**Break-even:** When trade is closed for a credit equal to the original debit.

**Management rules:**
- If short option ATM/OTM: roll when 4–10 DTE, same strike if neutral; OTM strike if mildly directional
- If short option ITM: roll to OTM strike if still within criteria; close if criteria no longer met
- IV spike: consider scaling out of partial position
- Final roll: if short option approaches long option expiration, the last roll creates a vertical spread — manage with vertical spread exit rules
- Close long option: 4–10 DTE before its expiration
- Position size: risk 1–2% of portfolio per trade

**Automation difficulty:** Medium-high. The strike differential between long and short options means rolling requires selecting a new strike, not just a new expiration. Claude needs clear rules for strike selection on each roll.

**vs. Calendar:** Diagonal has more directional bias → higher probability of success, higher max loss. Calendar is purer non-directional play.

---

### Strategy C: Iron Condor

**What it is:** Sell an OTM put vertical spread + sell an OTM call vertical spread on the same underlying, same expiration. Net credit received. Profit if underlying stays between the two short strikes.

Structure: Long put (lower) — Short put — [body] — Short call — Long call (higher)

**Greek profile:**
| Greek | Direction | Meaning |
|-------|-----------|---------|
| Delta | ~Neutral | Range-bound, not directional |
| Gamma | **Negative** | Accelerates against you near expiration |
| Theta | **Positive** | Time decay works in favor |
| Vega | **Negative** | Benefits from IV contraction |

**When to use:**
- High IV environment (top 50% of 52-week range — ideally IVR > 50)
- Underlying expected to stay range-bound (between support and resistance)
- IV expected to fall after entry
- Avoid upcoming earnings or major events within expiration window

**Entry parameters (from course):**
- Expiration: **20–50 DTE**
- Short options delta: **0.20–0.30** (both put and call sides)
- Short strikes: ideally at/outside support (put) and resistance (call) levels
- Long strikes: equidistant from short strikes; further OTM = cheaper, less protection
- Underlying: liquid, ≥ 1M avg daily volume; narrow bid/ask on all 4 legs
- IV Rank: **> 50** (high IV, expecting fall)
- No earnings within the expiration window

**Max profit:** Net credit received. Achieved if underlying stays between short strikes at expiration.
**Max loss:** Width of either wing (strike spread) minus net credit received. Fixed.
**Break-even points:** Short put strike − total credit; short call strike + total credit.

**Management rules:**
- If both spreads OTM: 
  - Consider closing 4–10 DTE (gamma risk increases)
  - Consider closing when 75–85% of max profit is captured
- If one short option ITM, > 20 DTE: monitor; let theta work; check calendar for events
- If one short option ITM, 10–20 DTE: begin scaling out (close partial position)
- If one short option ITM, < 10 DTE: **close entire position** — assignment risk is severe
- Monitor daily; pay close attention to ex-dividend dates (early call assignment risk)
- Position size: risk no more than 5% of portfolio per trade (higher than calendars due to max loss)

**⚠️ Pin risk:** If underlying closes exactly at a short strike at expiration, assignment is uncertain. Close before expiration to avoid.

**⚠️ Assignment risk is bilateral:** Unlike CSPs (only put side), iron condors can be assigned on either the call or put short. If assigned on short call without the long call, result is short stock — potentially unlimited downside. Always close before expiration if near short strikes.

**Automation difficulty:** Medium. Four legs to manage but same expiration simplifies tracking. The primary management decision is a binary: hold vs. close. Rolling individual legs is uncommon and complex.

**vs. Wheel:** Iron condor is vega negative (benefits from falling IV). Wheel (CSP) is vega negative too, but assignment delivers shares. Iron condor has fully defined max loss. Better fit for market-neutral conditions where you don't want share ownership.

---

### Strategy D: Long Call Butterfly Spread

**What it is:** Buy one lower-strike call + sell two middle-strike calls + buy one higher-strike call. All same expiration. Equidistant strikes. Net debit. Maximum profit when underlying is exactly at the middle strike at expiration.

Structure: Long call (lower) — 2× Short call (middle/body) — Long call (higher)

**Greek profile:**
| Greek | Direction | Meaning |
|-------|-----------|---------|
| Delta | ~Neutral (ATM body) | Non-directional when body is ATM |
| Gamma | **Negative** | Hurt by large price moves |
| Theta | **Positive** | Time works in favor when near body |
| Vega | **Negative** | Benefits from IV contraction |

**When to use:**
- High IV environment; IV expected to fall
- Underlying expected to stay near a specific price target (the body/short strike)
- When you have a specific price target, not just a range (more precise than iron condor)
- Avoid earnings within expiration window

**Entry parameters (from course):**
- Expiration: slightly beyond your forecast timeframe; avoid earnings
- Short/body strikes: **ATM** (or wherever you expect the stock to land at expiration)
- Long strikes: equidistant above and below the body
  - Wider wings = wider break-evens, higher potential gain, higher debit/risk
  - Narrower wings = cheaper, less risk, lower probability of profit
- IV Rank: **> 50** — high IV, expecting fall
- Underlying: liquid, sideways-trending, large-cap preferred
- Check support/resistance: short strikes should be at or near S/R levels

**Max profit:** (Middle strike − lower strike) − debit paid. Occurs only if stock is exactly at the middle strike at expiration.
**Max loss:** Net debit paid. Occurs if stock is below lower strike or above higher strike at expiration.
**Break-even:** Lower strike + debit; higher strike − debit.

**Management rules:**
- Close entire spread: **4–10 DTE** regardless of position (avoid assignment complexity)
- If spread OTM: close 4–10 DTE to salvage any remaining value
- If spread near body (ATM): close when 75–85% of max gain is captured, or at 4–10 DTE
- If spread fully ITM: close before expiration — all three legs ITM leads to exercise/assignment chaos
- Monitor daily; watch ex-dividend dates (early short call assignment risk)
- Position size: risk 1–2% of portfolio per trade

**⚠️ High-reward, lower probability:** Max profit requires very precise price prediction. The "tent" of profitability is narrow. Probability of achieving max gain is low — this is a defined-risk precision play, not a high-probability income trade.

**vs. Iron Condor:** Both vega negative and theta positive in high-IV environments. Iron condor has a wider profit range (the "body" between two strikes). Butterfly has a narrower profit zone but a higher reward-to-risk ratio. Iron condor is higher probability; butterfly is higher reward.

**Automation difficulty:** High. Three-legged position with narrow profit window. Requires precise price targeting. Max gain is highly sensitive to where the stock closes at expiration. Most difficult of the four to automate reliably.

---

## 4. Strategy Comparison Matrix

| | Calendar | Diagonal | Iron Condor | Butterfly |
|--|--|--|--|--|
| **Structure** | 2 legs, 2 expiries | 2 legs, 2 expiries | 4 legs, 1 expiry | 3 legs, 1 expiry |
| **Credit/Debit** | Debit | Debit | **Credit** | Debit |
| **IV Regime** | Low IV (rising) | Low IV (rising) | **High IV (falling)** | **High IV (falling)** |
| **Vega** | Positive | Positive | **Negative** | **Negative** |
| **Theta** | Positive | Positive | Positive | Positive |
| **Directional bias** | Neutral | Mild | Neutral | Neutral |
| **Max loss** | Debit paid | Debit paid | Wing width − credit | Debit paid |
| **Max gain** | Variable (with IV) | Variable (with IV) | Net credit (fixed) | Body width − debit |
| **Probability** | Moderate | Moderate-High | High | Low |
| **Automation difficulty** | Medium | Medium-High | Medium | High |
| **DTE target** | Long: 60-120 / Short: 20-40 | Long: 60-120 / Short: 20-40 | 20–50 | Until forecast + buffer |
| **Earnings avoidance** | Yes | Yes | Yes | Yes |

---

## 5. Implications for trade-pilot Architecture

### Shared Context Layer — What Every Strategy Needs

All four strategies share these context requirements with the wheel:

```python
# Shared macro context (read-only, all strategies)
shared_context = {
    "vix": 18.5,
    "vix_trend": "falling",           # "rising" | "falling" | "stable"
    "spx_trend": "neutral",            # "bullish" | "bearish" | "neutral"
    "fear_greed_index": 52,
    "market_regime": "low_iv_neutral"  # derived signal for strategy routing
}

# Per-underlying context (all strategies)
underlying_context = {
    "iv_rank": 42,
    "iv_percentile": 38,
    "iv_trend_5d": "rising",
    "iv_vs_hv_ratio": 1.15,
    "support_level": 185.0,
    "resistance_level": 195.0,
    "avg_daily_volume": 2100000,
    "days_to_earnings": 45,
    "next_ex_dividend_date": None
}
```

### Strategy Routing Signal

The shared context layer can include a `market_regime` field that routes the decision to the appropriate strategy:

```python
def determine_market_regime(iv_rank, vix, spx_trend):
    if iv_rank >= 50 and vix >= 20 and spx_trend == "neutral":
        return "high_iv_neutral"     # → Iron Condor or Butterfly
    elif iv_rank < 30 and spx_trend == "neutral":
        return "low_iv_neutral"      # → Calendar Spread
    elif iv_rank < 30 and spx_trend in ("bullish", "bearish"):
        return "low_iv_directional"  # → Diagonal Spread
    elif iv_rank >= 30:
        return "moderate_iv"         # → Wheel (CSP/CC)
    else:
        return "unclear"             # → No trade
```

This doesn't mandate a trade — it's a signal Claude uses when evaluating opportunities.

### Separate Account Mapping (as planned)

| Account | Strategy | IV Regime | DTE Range |
|---------|----------|-----------|-----------|
| Account 1 (main) | Wheel (CSP/CC) | Moderate-High IV | 21–35 |
| Account 2 | Iron Condor | High IV | 20–50 |
| Account 3 (future) | Calendar/Diagonal | Low IV | Short: 20-40, Long: 60-120 |
| Account 4 (future) | Butterfly | High IV | Until forecast |

Each account gets its own Claude prompt, its own state machine, and its own Alpaca paper account. All accounts read from the shared context layer.

---

## 6. Contradictions / Gaps

⚠️ **Calendar/diagonal max profit is not calculable in advance.** Trading platforms cannot estimate break-evens for multi-expiry spreads. Claude would need to be explicitly told this and not attempt to calculate fixed P&L targets for these strategies.

⚠️ **Iron condor pin risk is largely ignored in bot literature.** If a short strike closes exactly at expiration price, assignment may or may not occur. The bot must close iron condors before expiration to avoid this — the 4–10 DTE close rule handles this but must be hard-coded.

⚠️ **Butterfly automation is probably premature.** The narrow profit window, complex expiration scenarios, and low probability of max gain make butterflies very hard to automate well. Recommend starting with iron condors (cleaner, credit-based, wider profit zone) before attempting butterflies.

⚠️ **All four strategies explicitly require avoiding earnings.** This confirms that the earnings avoidance rule is universal — not just a wheel-specific rule. The shared context layer should include `days_to_earnings` for every strategy.

⚠️ **Position sizing formulas differ by strategy.** Calendar/diagonal: 1–2% portfolio risk. Iron condor: up to 5%. These need to be reflected in each strategy's prompt and guardrails.

---

## 7. Recommended Strategy Rollout Order

Based on automation difficulty, capital requirements, and edge clarity:

1. **Iron Condor** — Next after wheel. Credit-based (familiar structure), single expiry, clear management rules, high probability. Best fit for a separate high-IV account.
2. **Long Calendar Spread** — After iron condor. Requires multi-expiry tracking but management is conceptually simple. Deploy in low-IV environments.
3. **Long Diagonal Spread** — Extension of calendar. Adds directional bias. Build after calendar infrastructure is working.
4. **Long Butterfly** — Last. Highest automation complexity, lowest probability, most sensitive to precise price targeting. Paper trade heavily before deploying.

---

## 8. Synthesized Document

*This file is the synthesized document. Save as `knowledge/synthesized/non_directional_strategies.md`*
