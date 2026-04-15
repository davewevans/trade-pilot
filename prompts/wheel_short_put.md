## Current Phase: SHORT_PUT — Managing an Open Cash-Secured Put

You currently have an open short put position. Your task is to evaluate 
whether to hold, roll, or close the position early for profit.

The current position details are in context under "positions".
The current option snapshot (including live Greeks) is under "option_chain".

Work through this decision:
1. Calculate how much delta has changed since the position was opened
   - If current abs(delta) >= 2x initial abs(delta) → consider rolling
2. Calculate current premium as % of initial credit
   - If current price <= {{profit_target_pct}}% of initial credit → consider closing for profit
3. Check DTE remaining
   - If DTE <= {{close_dte_threshold}} and position is profitable → close now (gamma risk)
   - If DTE <= {{close_dte_threshold}} and position is at risk → roll out to next expiry
4. Check if earnings are approaching within the new expiry window
5. If none of the above trigger → hold

For a roll: find the best replacement contract in the put chain.
Target: same or lower strike, {{csp_dte_min}}-{{csp_dte_max}} DTE out, net credit if possible.

**Roll decision checklist:**
Before recommending a roll, verify ALL of these:
- [ ] The roll results in a net credit >= $0.10
- [ ] The new contract meets DTE {{csp_dte_min}}-{{csp_dte_max}} and delta -{{csp_delta_max}} to -{{csp_delta_min}}
- [ ] This would be roll #1 or #2 (not #3+)
- [ ] Earnings are > {{earnings_hard_block_csp_days}} days from the new expiry
- [ ] The stock hasn't dropped > 20% from original entry

If ANY check fails → recommend "close" instead of "roll".
