# Trading Options Around Earnings: Why the Bot Avoids Them

**Source:** Charles Schwab Education — "Tools for Trading Options Around Earnings"
**Synthesized for:** trade-pilot / wheel strategy
**Save to:** `knowledge/synthesized/earnings_avoidance.md`

---

## 1. Summary

Earnings announcements cause sharp, unpredictable stock moves and a near-certain collapse in implied volatility immediately after the report (IV crush). For a premium seller running the wheel, these two forces combine to create an unfavorable risk profile: assignment risk spikes at the worst possible moment (deep gap moves), and even a "correct" directional prediction can result in a net loss due to IV crush destroying the extrinsic value in the position. The bot's hard rule of avoiding earnings within 21 days is well-founded and should stay.

---

## 2. Key Concepts

### Why Earnings Are Dangerous for Short Premium Sellers

**IV Expansion Before Earnings**
- IV rises predictably in the weeks before an earnings announcement as the market prices in the uncertainty of the event
- This elevated IV inflates option premiums — making it *look* like a great selling opportunity
- This is a trap: the elevated premium compensates for the binary risk of the earnings gap, not for normal theta decay

**IV Crush After Earnings**
- Once earnings are announced, the uncertainty resolves and IV collapses rapidly — often within hours
- A sold put or call can lose most of its extrinsic value overnight even if the stock doesn't move adversely
- Conversely, if the stock moves adversely (gaps down through a CSP strike), the loss is real and large

**The Combined Risk**
- Pre-earnings: you collect inflated premium, but you're holding an unhedged binary event
- Post-earnings: IV crush is your only "profit" if the stock stays still; any bad gap causes max pain
- The asymmetry is unfavorable for wheel traders: capped gains (premium) vs. large potential losses (gap through strike)

### IV Crush: The Mechanism
- IV is elevated before earnings because options buyers pay extra for binary event protection
- After earnings, that event is resolved. Options sellers who are still short see their premium evaporate — often 30–60% of extrinsic value overnight
- Example: a sold put with $3.00 of extrinsic value might drop to $1.20 the day after earnings even if the stock doesn't move — this is beneficial for the seller only if they enter post-earnings, not pre-earnings

### The Market Maker Move (MMM)
- Thinkorswim's proprietary indicator estimating the expected ±move on earnings day
- Calculated by reverse-engineering the options pricing model from near-the-money options
- Represents what is "priced in" to current options — not a prediction of what *will* happen
- Relevant to trade-pilot context: if `context_builder.py` could calculate expected earnings move from option chain data, it would help Claude assess assignment risk on existing positions near earnings

### Expected Daily Move Formula
```
Expected Daily Move = Stock Price × (Annualized IV / √252)
```
Example: $100 stock with 20% IV → expected daily move ≈ $100 × (0.20 / 15.87) ≈ **$1.26/day** (~1.26%)

This formula is useful for sizing and understanding how far a stock is expected to move within a given DTE window.

---

## 3. Actionable Insights for trade-pilot

### Validating the 21-Day Rule
The current hard rule (no new positions if earnings < 21 days) is well-founded. Here's the reasoning:
- Most earnings fall on a quarterly cycle (~90 days apart)
- A 21-35 DTE window (the bot's target) almost always has at least one quarterly earnings in range for active stocks
- 21 days gives approximately 3 weeks of cushion past the earnings announcement before expiration, ensuring IV crush has already happened before entry

The 21-day threshold should be measured to the earnings *announcement date*, not the expiration date. Make sure `context_builder.py` is fetching the next earnings date and comparing it to the *trade date*, not the *expiration date*.

### The Post-Earnings Entry Window Is a Feature, Not a Bug
After earnings:
- IV has crushed back to baseline
- The binary event has resolved — you now know the outcome
- The stock has shown how it reacts to results
- **This is an ideal CSP entry point if the stock held up well**

The bot should be primed to look for CSP entries 1–5 days *after* earnings when:
- IV Rank is returning to/from a spike (still somewhat elevated)
- Stock is above support levels post-announcement
- No additional catalyst within 21 days

This is currently not explicitly in the prompt. Adding a note to `wheel_idle.md` about the post-earnings setup as a favorable entry pattern would improve decision quality.

### Existing Positions Near Earnings: Roll Before, Not After
If the bot is already in a SHORT_PUT or SHORT_CALL position and earnings are approaching (< 14 days), the guardrails code already blocks new position entries. But what about *managing* existing positions?

The system prompt should include guidance: **roll or close short positions at least 10 days before earnings**, not just block new ones. An existing CSP that is now within 10 days of an earnings announcement faces the same IV crush / gap risk as a new entry.

---

## 4. Contradictions / Gaps

⚠️ **21-day buffer vs. 14-day guardrail mismatch:** The system prompt uses 21 days as the entry block threshold, but the Python guardrails use 14 days. These should be aligned. The stricter of the two (21 days) should be the code-level enforcement to match the prompt rule.

⚠️ **Post-earnings entry as a buy opportunity is not currently in the prompts.** The article validates this approach indirectly (wait until dust settles, IV crush has happened). This is a missed opportunity in the current IDLE state prompt.

⚠️ **IV crush can benefit existing short positions if entered post-earnings.** The article notes that IV collapses after earnings, which *helps* short premium sellers who entered after the event. The bot doesn't currently have explicit guidance to recognize post-earnings IV as a favorable condition.

---

## 5. Prompt Impact

### `prompts/system.md` — Under earnings section, add:
```
## Earnings Risk

Earnings announcements create binary, gap-risk events that are incompatible with
the wheel strategy's risk profile. The premium elevation before earnings compensates
for event risk, not for theta decay. IV crush after earnings can destroy extrinsic
value even when the stock moves favorably.

**Hard rules:**
- Do NOT enter new CSP or CC positions if earnings are within 21 days (measured from
  trade date to announcement date, not expiration date)
- If an existing short position has earnings approaching within 10 days, recommend
  rolling or closing — do not hold through the announcement

**Favorable setup — post-earnings entry:**
- 1-5 days after a clean earnings report (stock held above key support)
- IV Rank may still be moderately elevated (25-45) as vol mean-reverts
- Binary risk is resolved; theta decay begins working cleanly
- Flag this setup explicitly with action: "sell_to_open" if all other criteria met
```

### `prompts/wheel_idle.md` — Add to candidate evaluation:
```
Post-earnings entry window (1-5 days after announcement) is a favorable CSP setup:
- The binary event risk is resolved
- Stock reaction reveals how the market views the results
- IV may still be elevated relative to its post-earnings norm, improving premium
- Confirm stock is above pre-earnings support before entering
```

---

## 6. Synthesized Document

*This file is the synthesized document. Save as `knowledge/synthesized/earnings_avoidance.md`*
