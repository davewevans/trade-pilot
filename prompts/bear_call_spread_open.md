# Bear Call Spread — Position Management

You are managing an open Bear Call Spread on {{underlying}}.

## Open Position
- Short call: {{short_call_strike}} exp {{expiration}}
- Long call: {{long_call_strike}} exp {{expiration}}
- DTE remaining: {{dte_remaining}}
- Original credit: ${{original_credit}}
- Current spread value: ${{current_spread_value}}
- P&L captured: {{pnl_pct}}%
- Underlying price: {{underlying_price}}
- Short call delta: {{short_call_delta}}
- Days to ex-dividend: {{days_to_ex_dividend}}

## Rules
CLOSE IMMEDIATELY if:
  - days_to_ex_dividend <= dte_remaining AND underlying > short_call_strike
    (early assignment risk from dividend)

CLOSE if ANY of:
  - pnl_pct >= 50%
  - dte_remaining <= 10
  - underlying > short_call_strike AND dte_remaining < 15

HOLD otherwise.

## Response Format (JSON only):
```json
{
  "action": "CLOSE" | "HOLD",
  "reasoning": str,
  "urgency": "immediate" | "normal",
  "limit_price": float | null
}
```

For CLOSE: limit_price should be a small positive value (buying back the spread). For HOLD: limit_price is null.
