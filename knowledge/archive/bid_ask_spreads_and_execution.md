# Bid/Ask Spreads in Volatile Markets: Execution Implications for trade-pilot

**Source:** Charles Schwab Education — "Large Bid/Ask Options Spreads in Volatile Markets"
**Synthesized for:** trade-pilot / wheel strategy
**Save to:** `knowledge/synthesized/bid_ask_spreads_and_execution.md`

---

## 1. Summary

Bid/ask spreads widen during volatile markets because market makers face greater uncertainty about the cost of their delta hedges. For trade-pilot, this has direct consequences: limit orders placed at the mid-price may not fill when spreads are wide, the effective premium collected on a CSP or CC is lower than the theoretical mid-price, and the bot's execution strategy (always limit orders) is correct but incomplete without guidance on how aggressively to price limit orders relative to the spread. Wide spreads during volatile markets can silently degrade the strategy's expected return per trade.

---

## 2. Key Concepts

### What Drives Bid/Ask Spreads
In priority order:

1. **Order flow and liquidity** — most important structural driver
   - High volume, balanced order flow → tight spreads (market makers compete)
   - Low volume, thin liquidity → wide spreads (market makers have more pricing power)
   
2. **Volatility of the underlying** — most *noticeable* driver during market stress
   - Volatile stock = uncertain hedge cost = market maker widens spread as a cushion
   - The spread covers expected slippage on the delta hedge the market maker must execute

3. **IV level** — higher IV means higher option prices which can mean larger absolute spread even if the *percentage* spread is constant

### The Market Maker Hedging Mechanism
- Market makers don't take directional bets — they stay delta-neutral via dynamic hedging
- When a customer sells 10 calls (delta = 0.30), the market maker buys them and goes long 300 delta
- To neutralize: market maker short-sells ~300 shares of the underlying
- In a volatile market, the market maker can't be certain of the fill price on those 300 shares
- That uncertainty is added to the bid/ask spread as a slippage buffer

**Key implication:** The more volatile the stock, the more the spread compensates the market maker for hedge uncertainty — and the more the seller pays in implicit transaction costs.

### How Wide Spreads Hurt Premium Sellers
When selling a CSP at the mid-price with a wide spread:
- Bid: $1.40 / Ask: $2.20 → Mid: $1.80
- A limit order at $1.80 may not fill because market makers have no obligation to trade at mid
- The realistic fill might be $1.50–1.60 — 20% less than the mid price expected
- Over many trades, this represents a meaningful drag on the strategy's expected return

For the wheel strategy specifically:
- CSP premiums already compress in low-IV environments
- Adding wide spreads on top of low premium = very thin actual yield
- Wide spreads + high IV is the "best" combination (high premium, but spread still hurts)
- Wide spreads + low IV = worst case (thin premium plus execution drag)

### Liquidity as an Entry Filter
The bot currently uses Open Interest >= 200 as a liquidity filter. This is a proxy for spread width — high OI options tend to have tighter spreads. However:

- OI is *static* (yesterday's data) — it doesn't reflect today's conditions
- During a volatile market day, even high-OI options can have temporarily wide spreads
- **Volume** (today's traded contracts) is a better real-time liquidity indicator than OI
- A bid/ask spread width check is the most direct filter possible

### When Spreads Are Worst
- Immediately at market open (market makers establishing positions)
- During major macro events (Fed announcements, CPI prints, geopolitical shocks)
- When VIX is elevated (> 25) — general market vol spills into individual options
- On illiquid underlyings even at normal vol
- Near expiration on options with low OI

---

## 3. Actionable Insights for trade-pilot

### The Bot's Limit Order Policy Is Correct — But Needs Execution Intelligence
All orders are limit orders (correct). But the prompt doesn't currently tell Claude *where to set the limit price* relative to the bid/ask. This is a significant gap.

**Recommended limit order pricing hierarchy (for CSP entry):**
1. Start at mid-price
2. If spread < $0.20: mid is fine — likely to fill
3. If spread $0.20–$0.50: use mid; willing to wait 2–3 minutes; if no fill, move $0.05 toward bid
4. If spread > $0.50: apply a minimum premium threshold — if mid-price yield doesn't meet minimums after accounting for half the spread, **skip the trade**

### Add Spread Width to the Context Package
Currently, `context_builder.py` fetches bid, ask, and mid from the option chain. The bot should calculate spread width explicitly and include it as a context field:

```python
"bid_ask_spread": round(ask - bid, 2),
"spread_pct_of_mid": round((ask - bid) / mid * 100, 1),  # as % of mid price
"spread_quality": "tight" if spread_pct < 10 else "moderate" if spread_pct < 20 else "wide"
```

**Claude should be prompted to reject contracts where `spread_pct_of_mid > 20%`.** Paying 20%+ of mid in implicit spread costs destroys the expected yield on a short-dated premium sale.

### Wide Spreads Signal Liquidity Risk
A wide spread is not just a transaction cost problem — it's also a signal that the position will be *hard to manage*:
- Rolling mid-spread will also be expensive
- Closing for a profit target (< 50% of credit) may be impractical if spread costs eat the target

When the bot is in SHORT_PUT or SHORT_CALL state and needs to roll, it should check current spread width before recommending a roll. If spreads are wide (> $0.40), the roll's net credit may be zero or negative even when a better strike is theoretically available.

### Liquidity Screen Enhancement
Replace or supplement OI >= 200 with a compound check:

```
Qualify contract if:
  open_interest >= 200
  AND daily_volume >= 50          # at least 50 contracts traded today
  AND bid_ask_spread_pct < 15%    # spread < 15% of mid
```

The volume filter catches situations where OI is high (from previous periods) but today's liquidity has dried up.

### Volatile Market Behavior Adjustment
When VIX > 25, spreads widen market-wide. The bot should:
- Apply a stricter spread filter (< 10% of mid vs. normal < 15%)
- Or reduce expected premium collected by 10–15% when calculating yield
- Or simply raise the IV Rank threshold (e.g., require IVR >= 40 instead of 30) since higher premium is needed to offset higher execution costs

---

## 4. Contradictions / Gaps

⚠️ **The bot's current prompts have no guidance on limit order pricing.** Claude returns a `limit_price` in its JSON response, but there's no instruction for how to derive it. Claude is presumably using mid-price — this is fine in normal markets but will cause fill failures in wide-spread/volatile environments.

⚠️ **Open interest as a liquidity proxy is imperfect.** High OI from prior sessions doesn't guarantee today's liquidity. Daily volume is a better real-time signal and is available from Alpaca's options data feed.

⚠️ **The system prompt doesn't address what to do when a limit order doesn't fill.** The bot places a limit order and... what happens next? Does it cancel? Retry at a worse price? Skip the trade? This decision tree should be explicit in the scheduler logic and communicated to Claude in the prompt.

---

## 5. Prompt Impact

### `prompts/system.md` — Add under "Execution" section:
```
## Limit Order Execution

All orders are limit orders. Set limit_price at the mid-price of the bid/ask spread.

**Spread quality check — before recommending a contract:**
- If bid_ask_spread_pct > 20%: SKIP this contract — execution costs will materially
  reduce effective premium collected and create adverse roll/close conditions
- If bid_ask_spread_pct 10-20%: acceptable; note in reasoning that spread is moderate
- If bid_ask_spread_pct < 10%: ideal — proceed normally

**In elevated volatility environments (VIX > 25):**
- Apply stricter spread filter: bid_ask_spread_pct must be < 12%
- Reduce expected yield assumptions by 10% to account for execution friction
- Prefer underlyings with daily_volume >= 100 contracts on target strike
```

### `prompts/wheel_idle.md` + `prompts/wheel_short_put.md` + `prompts/wheel_short_call.md` — Add to contract evaluation:
```
Contract liquidity check (required before selecting any contract):
1. open_interest >= 200
2. daily_volume >= 50 (if available in context)
3. bid_ask_spread_pct < 20% (calculated from bid/ask provided)
If any condition fails, skip this contract and look for an alternative strike/expiration.
```

---

## 6. Synthesized Document

*This file is the synthesized document. Save as `knowledge/synthesized/bid_ask_spreads_and_execution.md`*
