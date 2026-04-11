# Theta Decay: The Mechanical Foundation of the Wheel Strategy

**Source:** Charles Schwab Education — "Theta Decay in Options Trading: 3 Strategies"
**Synthesized for:** trade-pilot / wheel strategy
**Save to:** `knowledge/synthesized/theta_decay_and_the_wheel.md`

---

## 1. Summary

The wheel strategy is, at its core, a theta harvesting strategy. Every CSP and covered call the bot sells is a bet that time decay will erode the option's extrinsic value faster than adverse price movement can build intrinsic value against the position. Understanding theta's non-linear behavior — specifically its acceleration near expiration and its relationship to moneyness — directly informs where the bot should sell (OTM, not ATM), when (21-35 DTE, not 0-7 DTE), and when to close (at 50% of max profit, not at expiration).

---

## 2. Key Concepts

### What Theta Is
- **Theta** = the expected daily dollar decrease in an option's value due to the passage of time alone (all else equal)
- Theta is always negative for long options, always positive for short options
- A short put with theta = +0.05 earns approximately $5/day per contract from time decay alone
- In practice, theta follows a **non-linear, accelerating curve** — not a straight line

### The Theta Curve: Non-Linear Decay

```
Option Value
│
│\
│ \
│  \
│   \________
│             \_______________________
│                                     \___
└──────────────────────────────────────────── Time to Expiry
 90 DTE        45 DTE        21 DTE   7 DTE  0
```

- Far from expiration (> 45 DTE): theta decay is slow and relatively flat
- In the middle zone (21–45 DTE): decay accelerates — this is the **sweet spot for selling**
- Near expiration (< 7 DTE): decay is maximum, but so is gamma risk (large delta swings)

**This is why the bot targets 21–35 DTE: it enters when theta acceleration has begun but before gamma risk becomes dominant.**

### Moneyness and Theta
- **ATM options have the highest theta** — they carry the most extrinsic value and therefore decay fastest in absolute dollar terms
- **OTM options have lower absolute theta** but also lower assignment/delta risk
- **ITM options have low theta** relative to their price because most value is intrinsic (which doesn't decay)

For the wheel strategy:
- CSPs at delta -0.20 to -0.30 are OTM → lower absolute theta but acceptable premium for the risk
- ATM puts would decay faster but have ~0.50 delta → 50% assignment probability, too aggressive for wheel
- The delta target implicitly controls the theta/risk tradeoff

### Extrinsic Value = What the Bot Sells
- Total option premium = **intrinsic value** + **extrinsic value**
- Only extrinsic value decays. Intrinsic value moves with the stock — it is not "earned" through time passage
- When a CSP is OTM, it has **no intrinsic value** — 100% of the premium is extrinsic → 100% is theta-driven income
- When the position goes ITM, intrinsic value builds and theta income is offset by delta losses

### The 50% Profit Close Rule — Theta-Driven Rationale
The bot closes positions when premium decays to 50% of the original credit. Here's why this is rational:

| DTE at entry | DTE at 50% decay (approx.) | % of theta harvest captured |
|---|---|---|
| 35 DTE | ~25 DTE | ~71% of the time elapsed |
| 30 DTE | ~20 DTE | ~67% |
| 21 DTE | ~14 DTE | ~67% |

The first 50% of decay happens over roughly 2/3 of the time period. The second 50% happens in the final 1/3 — but with **much higher gamma risk**. Closing at 50% captures most of the theta income while avoiding the dangerous final stretch. This is mathematically sound.

### The Gamma Risk Tradeoff Near Expiration
- As DTE approaches zero, **gamma increases sharply** for OTM options
- Gamma = rate of change of delta. High gamma means small stock moves create large delta swings
- A CSP at 5 DTE can go from delta -0.10 to delta -0.70 in a single bad day
- This is why the bot's roll trigger "DTE <= 7 and at risk → roll out" exists — it avoids the gamma explosion zone

**The practical rule: theta works for you, gamma works against you. The 21-35 DTE zone maximizes theta benefit and minimizes gamma exposure.**

### Short OTM Vertical Spread (Adjacent Strategy)
The article discusses vertical spreads as a theta strategy. For context:
- A short put vertical (bull put spread) = sell OTM put + buy further OTM put
- Same theta mechanics as a naked CSP, but with defined maximum loss
- Lower premium collected, but no catastrophic downside if stock gaps far below
- Relevant if the bot ever needs to manage a position where the underlying becomes too risky to hold outright

---

## 3. Actionable Insights for trade-pilot

### The Bot's DTE Target Is Correct — Don't Drift Outside It
21–35 DTE is the empirically validated sweet spot for theta selling. Specific risks if Claude drifts outside:

**Too long (> 45 DTE):**
- Theta decay is slow — capital is tied up for a slow trickle
- More time for adverse moves to develop
- Lower annualized return on premium

**Too short (< 14 DTE):**
- Theta is fast, but gamma is dangerous
- A bad day can swing the position from safe to max-loss territory rapidly
- Harder to roll for a credit when DTE is low (less time value in the new position)

**Prompt hardening:** Add explicit language that DTE outside 21–35 is a reject condition, not just a preference.

### The 50% Profit Close Is Mathematically Justified — Enforce It
The current rule says close when premium <= 50% of initial credit. The theta curve analysis validates this. Add to the prompt the explicit rationale: you've captured ~70% of the theoretical theta income with ~30% of the risk remaining. Close it.

Edge case: what if the remaining premium is very small in absolute terms (e.g., $0.10)? If closing costs $0.65 in commissions and the remaining premium is $0.10, holding to expiration is better economically. Add a minimum buyback threshold:

```
Close early (50% profit target) UNLESS:
  remaining_premium < $0.15 AND DTE > 5
  In that case: hold to expiration (commission cost > value of closing)
```

### Theta as a Decision-Making Input
Claude currently has access to option chain data but may not have explicit theta values for the target contract. Adding theta to the context package gives Claude a concrete daily income number to reason about:

**Suggested context field:**
```python
"contract_theta": -0.04,      # daily decay value (negative for long, positive logic for short)
"daily_theta_income": 4.00,   # theta × 100 shares = daily $ income from time decay
"theta_to_premium_ratio": round(abs(theta) / mid_price, 3)  # theta efficiency
```

The `theta_to_premium_ratio` is particularly useful: a ratio of 0.03 means you earn 3% of the option's value per day from theta — a rough efficiency metric.

### Gamma Risk Near Expiration — Roll Trigger Refinement
The current roll trigger "DTE <= 7 and at risk" is vague about what "at risk" means. Using the theta/gamma framework:

**More precise trigger:**
```
Roll when ANY of:
  1. DTE <= 7 AND option delta has doubled from initial (e.g., started at -0.25, now at -0.50)
  2. DTE <= 7 AND stock within 1 ATR of strike
  3. DTE <= 5 AND option is ITM by any amount
  4. Premium <= 50% of initial credit (profit target — always close regardless of DTE)
```

---

## 4. Contradictions / Gaps

⚠️ **The prompt says "close when premium <= 50% of initial credit" but doesn't address the commission break-even case.** For very cheap buybacks (< $0.15), commissions may make closing uneconomical. Add a minimum-threshold carve-out.

⚠️ **The prompt's DTE range (21-35 days) is stated as a preference, not a hard reject.** Based on the theta curve analysis, < 21 DTE entries should be hard rejections, not just less preferred. The gamma risk profile changes materially below 21 DTE.

⚠️ **The prompts don't reference theta explicitly as a metric Claude should look at when selecting strikes.** Delta is mentioned, premium is mentioned, but theta-as-daily-income is not. Adding theta to context and mentioning it in prompts would improve strike selection rationale.

---

## 5. Prompt Impact

### `prompts/system.md` — Add under "Premium Selling Mechanics":
```
## Theta: The Mechanism Behind Premium Income

The wheel strategy profits from theta (time decay). Every day, a short option loses
extrinsic value. This decay is:
- Non-linear: it accelerates as expiration approaches
- Fastest for ATM options; slower for deep OTM or ITM options
- The sweet spot is 21-35 DTE: decay has meaningfully accelerated but gamma
  risk (wild delta swings near expiry) hasn't become dangerous yet

**Key theta principles for decision-making:**
1. All premium collected is extrinsic value — it decays to zero by expiration if OTM
2. The first 50% of premium decay happens over ~70% of the time period
3. The last 50% happens in ~30% of the time with much higher gamma risk
4. Close at 50% profit to capture most theta income and avoid late-DTE gamma exposure

**DTE rules (hard constraints, not preferences):**
- DTE < 21: DO NOT enter new positions (gamma risk outweighs theta benefit)
- DTE > 35: DO NOT enter new positions (theta decay too slow, capital efficiency poor)
- DTE 21-35: target zone — enter here
```

### `prompts/wheel_short_put.md` + `prompts/wheel_short_call.md` — Refine roll trigger:
```
Roll or close triggers (in priority order):
1. Premium <= 50% of initial credit → close for profit (unless remaining premium < $0.15 — hold to expiry)
2. Delta has doubled from entry delta → roll (position is moving against you faster than theta works)
3. DTE <= 7 AND stock within 1 ATR of strike → roll out
4. DTE <= 5 AND option is ITM → roll out immediately
```

---

## 6. Synthesized Document

*This file is the synthesized document. Save as `knowledge/synthesized/theta_decay_and_the_wheel.md`*
