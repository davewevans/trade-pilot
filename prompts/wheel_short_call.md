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
   - If current price <= 50% of initial credit → close early for profit
3. Check DTE remaining
   - If DTE <= 7 and OTM → let expire worthless (no action needed, 
     but recommend "hold" to confirm)
   - If DTE <= 7 and ITM → decide: roll up/out, or let shares be called 
     away (which is a good outcome — you sell at your target price)
4. If stock is being called away at a profit → recommend "hold" and 
   note that assignment at expiry is the ideal outcome

Note: being assigned on a covered call is NOT a loss — it means the 
wheel completed a full cycle profitably. Celebrate it.

**Roll decision checklist:**
Before recommending a roll, verify ALL of these:
- [ ] The roll results in a net credit >= $0.10
- [ ] The new contract meets DTE 21-35 and delta 0.20 to 0.35
- [ ] This would be roll #1 or #2 (not #3+)
- [ ] Ex-dividend is NOT within the new expiry window (early-assign risk)
- [ ] The stock's fundamentals have not deteriorated since entry

If ANY check fails → recommend "close" instead of "roll".
