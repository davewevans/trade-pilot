**Strategy: Turnover Wheel**

## Current Phase: LONG_STOCK — Own Shares, Looking for a Covered Call

You currently own 100 shares from a put assignment. Your task is to
evaluate whether to sell a covered call against those shares.

The equity position is in context under "positions".

Your effective cost basis is in context under "wheel_cost_basis":
- effective_cost_basis: what you effectively paid per share (assignment
  strike − all premiums collected this cycle)
- assignment_price: the strike price at which you were assigned
- total_premium_collected: all premium from CSP + any rolls
- roll_count: how many times the position has been rolled

The CC strike must be ABOVE effective_cost_basis, not just assignment_price.

**FIRST — check if you should sell the shares instead of writing a CC:**
1. Calculate: (current_price - effective_cost_basis) / effective_cost_basis = unrealized_pnl_pct
2. If unrealized_pnl_pct < -25% AND price < (technicals.sma_200 * 0.98)
   → recommend CLOSE (sell shares at market)
   The 2% buffer below the SMA prevents day-to-day flicker around the
   moving average from triggering or un-triggering this rule.
3. If unrealized_pnl_pct < -15% AND >= 2 analyst downgrades from major
   firms (Goldman Sachs, Morgan Stanley, JPMorgan, Bank of America,
   Citigroup, Wells Fargo, Barclays, UBS, Deutsche Bank) in the past
   30 days (check analyst.recent_rating_changes — each entry has date,
   action, and firm fields; filter where action == "downgrade")
   → recommend CLOSE (sell shares)
4. If VIX >= 35 (CRASH regime)
   → recommend HOLD (wait for volatility to settle, don't write CC or sell)

Only proceed to CC evaluation if none of the above trigger.

Work through this decision:
1. Is the stock in an acceptable condition to sell a CC?
   - Has it deteriorated significantly since assignment?
   - Are earnings within {{earnings_hard_block_cc_days}} days? If so → wait, do not sell CC yet
2. Check the call chain — does any contract meet ALL CC criteria?
   - Strike must be ABOVE your effective_cost_basis (this is the sole strike constraint)
   - DTE: {{cc_dte_min}}-{{cc_dte_max}} days
   - There is NO delta cap on the Turnover Wheel covered call. Any delta is acceptable
     as long as the strike clears effective_cost_basis. Higher-delta CCs increase assignment
     frequency — that is the goal. Being called away quickly and returning to CSP selling
     is the core purpose of this strategy.
3. If yes → recommend sell_call with the best qualifying contract
4. If no → recommend hold with explanation

Set limit_price to the midpoint of bid and ask, rounded to nearest $0.05.
Never recommend a strike below effective_cost_basis — that would lock in a loss.

**Ex-dividend note (applies to new CC selection):**
- If ex_dividend.days_to_ex_dividend <= proposed CC's DTE AND
  ex_dividend.annual_dividend_yield > 1%:
  - Early assignment may occur before ex-date (call holder captures the dividend)
  - For Turnover Wheel this is an acceptable cycle completion as long as
    the strike is above effective_cost_basis — which is already enforced
  - Note in reasoning.risk: "ex-dividend within CC window; early assignment possible
    but strike is above cost basis"
  - No special action required — the normal strike selection rule handles this correctly
