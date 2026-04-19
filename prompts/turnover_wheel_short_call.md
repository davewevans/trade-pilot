**Strategy: Turnover Wheel**

## Current Phase: SHORT_CALL — Managing an Open Covered Call

**Counterfactual check:** Before recommending HOLD, ask: if this position were not already open, would you recommend opening it right now? If no, recommend CLOSE regardless of current P&L.

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

**Ex-dividend risk check (required before any CC):**
- Check ex_dividend.days_to_ex_dividend from context
- If days_to_ex_dividend <= DTE of the proposed CC:
  - AND the stock pays a meaningful dividend (annual_dividend_yield > 1%)
  - → Flag in reasoning: "ex-dividend within CC window"
  - → Prefer a strike that is OTM enough that early assignment is unlikely
  - → If the best CC candidate would be ITM at the ex-div date, SKIP

**Roll decision checklist:**
Before recommending a roll, verify ALL of these:
- [ ] The roll results in a net credit >= $0.10
- [ ] The new contract meets DTE {{cc_dte_min}}-{{cc_dte_max}} and delta within 0.45
- [ ] This would be roll #1, #2, or #3 (not #4+)
- [ ] Ex-dividend is NOT within the new expiry window (early-assign risk)
- [ ] The stock's fundamentals have not deteriorated since entry

If ANY check fails → recommend "close" instead of "roll".
