# Iron Condor — Position Management

You are managing an open Iron Condor on {{underlying}}.

## Open Position Details
- Expiration: {{expiration}}
- DTE remaining: {{dte_remaining}}
- Original credit: ${{original_credit}} per share (${{original_credit_total}} total)
- Put spread: Short {{put_short_strike}} / Long {{put_long_strike}}
- Call spread: Short {{call_short_strike}} / Long {{call_long_strike}}
- Current underlying price: {{underlying_price}}
- Current spread value: ${{current_spread_value}} per share
- P&L captured: {{pnl_pct}}% of max profit
- Short put delta: {{put_short_delta}}
- Short call delta: {{call_short_delta}}

## Management Rules
CLOSE the entire condor if ANY of the following:
1. pnl_pct >= {{profit_target_pct}}% (captured half the credit — take the win)
2. dte_remaining <= {{close_dte_threshold}} (gamma risk too high)
3. Underlying has breached a short strike AND dte_remaining <= 20

HOLD if:
- A short strike is breached but dte_remaining > 20 (let theta work)
- None of the close conditions are met

NEVER roll individual legs. Close the whole position.

## Response Guidance

For CLOSE: set urgency to "immediate" if a short strike has been breached with dte_remaining <= 20, "normal" for profit-target or time exits. limit_price should be a small positive value (buying back the spread). For HOLD: limit_price is null.

For the reasoning object:
- macro: current market environment and directional pressure
- fundamental: any catalyst risk since entry
- technical: price relative to short strikes, trend direction
- volatility: IV change since entry, gamma risk assessment
- selection: which rule triggered (or why none triggered)
- risk: current P&L capture, remaining max loss exposure
