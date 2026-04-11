# Options Assignment: Mechanics, Risk, and Wheel Strategy Implications

**Sources:** Charles Schwab Education — "Risks of Options Assignment" + "Options Exercise, Assignment, and More: A Guide"
**Synthesized for:** trade-pilot / wheel strategy
**Save to:** `knowledge/synthesized/assignment_mechanics.md`

---

## 1. Summary

Assignment is the mechanism by which the wheel strategy transitions between states: a short put being assigned delivers 100 shares (IDLE → LONG_STOCK), and a short call being assigned removes those shares (LONG_STOCK → IDLE). For trade-pilot, assignment is not a failure — it is a designed outcome of the wheel. However, *early* assignment carries unique risks that the bot must be aware of: it can happen at any time on American-style options, most commonly around ex-dividend dates, when deep ITM, or when extrinsic value has decayed to near zero.

---

## 2. Key Concepts

### Assignment Basics
- **Assignment** obligates the short option seller to fulfill the contract terms
  - Short put assigned → must **buy** 100 shares at the strike price
  - Short call assigned → must **sell** 100 shares at the strike price
- American-style options (all equity options the bot trades) can be assigned **at any time** before expiration — not just at expiration
- ITM options have higher early assignment risk than OTM options

### Intrinsic vs. Extrinsic Value and Assignment Likelihood
- An option's total premium = **intrinsic value** + **extrinsic value**
- Intrinsic value = amount the option is ITM (e.g., stock at $45, put strike at $50 → $5 intrinsic)
- Extrinsic value = remaining time value + IV component
- **A long option holder will almost never exercise early while extrinsic value remains**, because they'd be giving up that remaining value
- Early assignment happens when extrinsic value approaches zero — typically when the option is deep ITM close to expiration, or just before an ex-dividend date

### Three Early Assignment Triggers (in priority order)
1. **Ex-dividend date (most common for calls):** If a short ITM call's extrinsic value is *less than the upcoming dividend*, the call buyer rationally exercises early to capture the dividend. The call seller then must deliver shares and loses the right to the dividend.
2. **Deep ITM with near-zero extrinsic value:** When an option is so far in the money that virtually all its value is intrinsic, the holder may exercise rather than sell (especially with illiquid options where bid/ask spread > extrinsic value).
3. **Short put, deep ITM, close to expiration with wide underlying bid/ask:** The buyer exercises to take the shares at the strike rather than sell the put at a potentially unfavorable price.

### Expiration Scenarios Quick Reference

| Position | Stock > Strike | Stock < Strike |
|----------|---------------|----------------|
| Long call | ITM → auto-exercised | OTM → expires worthless |
| **Short call** | **ITM → assigned (must sell shares)** | OTM → expires worthless |
| Long put | OTM → expires worthless | ITM → auto-exercised |
| **Short put** | OTM → expires worthless | **ITM → assigned (must buy shares)** |

*Note: auto-exercise threshold is $0.01 ITM at expiration.*

### At-Expiration Auto-Exercise
- The OCC automatically exercises any option that is $0.01 or more ITM at expiration
- The option holder can submit a "Do Not Exercise" (DNE) request, but this is rare
- For the bot: any short put that expires with the stock below the strike will result in assignment and share delivery — this is the expected wheel outcome

### Rolling vs. Closing: The Three Choices Near Expiration
1. **Do nothing** — let the option expire, accept assignment or expiry as appropriate
2. **Close early** — buy back the short option; no assignment risk, premium kept minus buyback cost
3. **Roll** — close current position + open new one at further DTE (and possibly different strike)

---

## 3. Actionable Insights for trade-pilot

### Assignment Is a State Transition, Not a Failure
The bot's wheel cycle is designed around assignment. When a CSP is assigned:
- The bot transitions from SHORT_PUT → LONG_STOCK
- Cost basis = strike price − premium received (the bot should track this correctly)
- The assignment is logged as an NTA (Non-Trade Activity) event in Alpaca

The bot needs to handle the NTA event reliably. If assignment happens overnight (most common), the scheduler's next `market_open` job should detect the new share position and transition state.

### Early Assignment Risk Is Highest in Two Scenarios
1. **Short calls near ex-dividend date:** If the bot holds a covered call and the underlying has an ex-dividend date approaching, and the call is ITM with less extrinsic than the dividend amount — early assignment is likely. The bot should check upcoming ex-div dates and flag this risk.
2. **Short puts that become deep ITM during a sharp selloff:** If the underlying drops far below the CSP strike with little DTE remaining, early assignment could happen before the bot's next decision cycle runs.

**Suggested context field for `context_builder.py`:**
```python
"next_ex_dividend_date": "2026-04-25",  # or None
"days_to_ex_dividend": 14,
"short_option_extrinsic_value": 0.35,   # remaining extrinsic on open position
```

This would let Claude assess early assignment risk explicitly.

### The Extrinsic Value Rule
The articles confirm a key principle: **buyers almost never exercise early while meaningful extrinsic value remains**. This has a direct implication for roll decisions:

- If the bot's short put has extrinsic value > $0.50, early assignment risk is low — hold or manage normally
- If extrinsic value < $0.20 and the option is ITM → assignment risk is elevated → consider closing or rolling

This aligns with the existing roll trigger "DTE <= 7 and at risk → roll out" but adds a more precise trigger. At DTE 7, most ATM options have about $0.20–0.40 of extrinsic left; deep ITM options may have near zero.

### Post-Assignment: Tracking Cost Basis Correctly
When assigned on a CSP:
- **Cost basis** = strike price − total premium received
- Example: sold $50 put for $1.50 credit → assignment → cost basis = $48.50
- The bot must track this precisely for the covered call rule: "strike must be above cost basis"
- If the stock drops further after assignment, cost basis becomes the key guardrail preventing a locked-in loss on the CC

### What Happens If the Bot Misses an Assignment Event
If an NTA event is missed (e.g., Alpaca webhook failure, scheduler down):
- The bot thinks it's in SHORT_PUT state
- The position is actually LONG_STOCK
- Next job run should catch this via portfolio position check
- `scheduler.py` should reconcile wheel state from *actual Alpaca positions*, not just internal state, on every run

---

## 4. Contradictions / Gaps

⚠️ **The articles focus on assignment as a risk, but for wheel traders it is the designed mechanism.** The language in the prompts should reflect this: assignment on a CSP is not a loss event — it is the wheel working as intended. Claude should not recommend avoiding assignment at all costs; it should evaluate whether the assigned cost basis is acceptable.

⚠️ **Ex-dividend risk on covered calls is not currently in the system prompt.** If the bot sells a CC on a stock with an upcoming dividend and the call goes ITM, early call assignment risk exists. This is particularly relevant if the underlying is a dividend-paying stock.

⚠️ **The 21-day earnings rule and the ex-dividend consideration are separate risks that both affect short calls.** Currently only earnings is in the prompt. Ex-div date should also be checked before entering a CC, especially if the underlying pays a meaningful dividend.

---

## 5. Prompt Impact

### `prompts/system.md` — Add under "Assignment" section:
```
## Assignment Mechanics

Assignment on a cash-secured put is the designed wheel transition to LONG_STOCK.
It is not a failure — evaluate the resulting cost basis and proceed.

**Cost basis after CSP assignment:**
cost_basis = strike_price - total_premium_received

**Early assignment risk factors (American-style options):**
- Option is deep ITM with extrinsic value < $0.20
- Ex-dividend date is approaching and the short call's extrinsic < dividend amount
- DTE <= 7 with option still ITM

**If early assignment risk is elevated:**
- For short puts: roll down-and-out before extrinsic decays to near zero
- For short calls: close or roll before ex-dividend date if call is ITM

**Assignment state transition:**
- CSP assigned → wheel state transitions to LONG_STOCK; log cost basis
- CC assigned → wheel state transitions to IDLE; log realized P&L
```

### `prompts/wheel_short_call.md` — Add:
```
Check for ex-dividend risk before holding a short call:
- If next_ex_dividend_date is within 21 days AND the call is ITM
- AND short_option_extrinsic_value < dividend_per_share
- → STRONG recommendation: close or roll the call before the ex-dividend date
  to avoid early assignment and loss of dividend
```

---

## 6. Synthesized Document

*This file is the synthesized document. Save as `knowledge/synthesized/assignment_mechanics.md`*
