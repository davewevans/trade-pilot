# Long Call Vertical — Position Management

You are managing an open Long Call Vertical on {{underlying}}.

## Open Position
- Long call: {{long_call_strike}} exp {{expiration}}
- Short call: {{short_call_strike}} exp {{expiration}}
- DTE remaining: {{dte_remaining}}
- Original debit: ${{original_debit}}
- Current spread value: ${{current_spread_value}}
- Gain captured: {{gain_pct}}% of max gain
- Underlying price: {{underlying_price}}
- Break-even: {{break_even}}
- Price target: {{price_target}}

## Rules

CLOSE for profit if:
  - gain_pct >= 75% of max gain (don't get greedy)
  - Underlying has reached price_target

CLOSE for time-based exit if:
  - dte_remaining <= 20 (gamma risk and theta accelerate)

CLOSE for loss (stop) if:
  - Current spread value has fallen 40% from original debit
    (e.g. paid $1.25, now worth < $0.75)

HOLD if none of the above apply.

## Response Guidance

For CLOSE: limit_price is positive (selling the spread to close). For HOLD: limit_price is null.

For the reasoning object:
- macro: current market environment and bullish thesis status
- fundamental: any developments since entry
- technical: price relative to break-even and price target
- volatility: IV change since entry (IV decrease hurts long options)
- selection: which exit rule triggered (or why none triggered)
- risk: current gain/loss vs original debit, remaining time value
