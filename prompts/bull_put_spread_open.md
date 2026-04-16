# Bull Put Spread — Position Management

**Counterfactual check:** Before recommending HOLD, ask: if this position were not already open, would you recommend opening it right now? If no, recommend CLOSE regardless of current P&L.

You are managing an open Bull Put Spread on {{underlying}}.

## Open Position
- Short put: {{short_put_strike}} exp {{expiration}}
- Long put: {{long_put_strike}} exp {{expiration}}
- DTE remaining: {{dte_remaining}}
- Original credit: ${{original_credit}}
- Current spread value: ${{current_spread_value}}
- P&L captured: {{pnl_pct}}%
- Underlying price: {{underlying_price}}
- Short put delta: {{short_put_delta}}

## Rules
CLOSE if ANY of:
1. pnl_pct >= {{profit_target_pct}}% (captured half the credit — take the win)
2. dte_remaining <= {{close_dte_threshold}} (gamma risk)
3. Underlying < short_put_strike AND dte_remaining < 15 (cut loss early)

HOLD otherwise. Let theta decay work.

Unlike the wheel, assignment does NOT transition to LONG_STOCK.
The long put caps loss — close the whole spread instead.

## Response Guidance

For CLOSE: limit_price should be a small positive value (buying back the spread cheaply). For HOLD: limit_price is null.

For the reasoning object, address each dimension briefly:
- macro: current market environment
- fundamental: any company developments since entry
- technical: price action relative to short strike
- volatility: IV change since entry
- selection: which rule triggered (or why none triggered)
- risk: current P&L capture and remaining downside
