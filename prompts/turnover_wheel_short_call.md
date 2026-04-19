**Strategy: Turnover Wheel**

## Current Phase: SHORT_CALL — Managing an Open Covered Call

You currently have an open short call position against 100 shares.
Your task is to evaluate whether to hold, roll, or let it expire/assign.

The current position details are in context under "positions".
The current option snapshot is under "option_chain".

Work through this decision:
1. Calculate delta change since opening
   - If current delta >= 2x initial delta → stock has rallied, consider
     rolling up and out for more premium
2. Calculate current premium vs initial credit
   - If current price <= {{profit_target_pct}}% of initial credit → close early for profit
3. Check DTE remaining
   - If DTE <= {{close_dte_threshold}} and OTM → let expire worthless (no action needed,
     but recommend "hold" to confirm)
   - If DTE <= {{close_dte_threshold}} and ITM → decide: roll up/out, or let shares be called
     away (which is a good outcome — you sell at your target price)
   Note: {{close_dte_threshold}} DTE is tighter than the standard wheel because this
   strategy uses 7–14 DTE contracts — gamma risk arrives sooner.
4. If stock is being called away at a profit → recommend "hold" and
   note that assignment at expiry is the ideal outcome

Note: being assigned on a covered call is NOT a loss — it means the
turnover wheel completed a full cycle profitably. Celebrate it.

**Ex-dividend risk on the CURRENT short CC:**
- If ex_dividend.days_to_ex_dividend <= DTE of current position AND
  ex_dividend.annual_dividend_yield > 1%:
  - Calculate the CC's extrinsic value: current_call_price − max(0, current_price − strike)
  - Estimate the next dividend: current_price × (annual_dividend_yield / 4)
  - If estimated dividend > extrinsic value, early assignment before ex-date is rational
    for the call holder to execute
  - For Turnover Wheel, early assignment is an acceptable cycle completion
    (strike is already above effective_cost_basis). Recommend HOLD and note
    "prepared for early assignment before ex-dividend" in reasoning.risk
  - Do NOT attempt to roll in this scenario — rolling would re-expose the position
    to the same early-assignment dynamic

**Ex-dividend check on a ROLL CANDIDATE:**
- Apply the same dividend-vs-extrinsic test to the proposed new CC
- If early assignment is likely on the new contract and the new strike
  would still be above effective_cost_basis, the roll is acceptable but
  notable — flag it in reasoning
- If early assignment is likely and the new strike would NOT clear
  effective_cost_basis, reject the roll (the cost basis rule is absolute)

**Roll decision checklist:**
Before recommending a roll, verify ALL of these:
- [ ] The roll results in a net credit >= $0.10
- [ ] The new contract meets DTE {{cc_dte_min}}-{{cc_dte_max}}
- [ ] The new strike is above effective_cost_basis (absolute rule)
- [ ] This would be roll #1, #2, or #3 (not #4+)
- [ ] The stock's fundamentals have not deteriorated since entry

If ANY check fails → recommend "close" instead of "roll".
