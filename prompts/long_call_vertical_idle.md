# Long Call Vertical — Entry Evaluation

You are evaluating whether to open a Long Call Vertical (bull call
debit spread) on {{underlying}}.

Buy a lower-strike call (ATM or ITM) and sell a higher-strike OTM
call. Net debit paid. Profit if underlying rises above break-even
(long strike + debit) before expiration. This is a SPECULATIVE,
DIRECTIONAL strategy. Only deploy with strong bullish evidence.

## Context
- Underlying price: {{underlying_price}}
- Confirmed market regime: {{confirmed_market_regime}}
- IV Rank: {{iv_rank}} ({{iv_environment}})
- Support bounce signal: {{support_bounce_signal}}
- Above 50-day SMA: {{above_50sma}}
- Days to earnings: {{days_to_earnings}}
- IV overvaluation: {{iv_overvalued_label}}

## Best Long Call Vertical Candidate
{{best_candidate}}

## Decision Rules
OPEN if ALL of the following:
1. confirmed_market_regime is BULL
2. iv_environment is LOW (IVR < 30)
3. support_bounce_signal.cahold_detected is true
4. above_50sma is true
5. days_to_earnings > DTE + {{earnings_buffer_days_beyond_dte}} days buffer
6. best_candidate.net_debit < {{max_net_debit}}
7. best_candidate.long_leg.delta between {{long_delta_min}} and {{long_delta_max}}
8. DTE between {{dte_min}} and {{dte_max}}
9. iv_overvalued_label should be UNDERVALUED or FAIR. If OVERVALUED,
   skip — you are overpaying for the long call.

SKIP if:
- Market regime is not BULL
- IV is not LOW (don't buy expensive options)
- No CAHOLD signal detected (need technical confirmation)
- iv_overvalued_label is OVERVALUED (ORATS confirms options are expensive)

## Required Response Format (JSON only):
```json
{
  "action": "OPEN" | "SKIP",
  "long_call_symbol": str,
  "short_call_symbol": str,
  "expiration": str,
  "dte": int,
  "long_call_strike": float,
  "short_call_strike": float,
  "net_debit": float,
  "max_gain": float,
  "break_even": float,
  "limit_price": float,
  "price_target": float,
  "reasoning": str,
  "skip_reason": str | null
}
```

IMPORTANT: limit_price must be POSITIVE for debit spreads. For example, 1.25 means you pay $1.25 debit.
