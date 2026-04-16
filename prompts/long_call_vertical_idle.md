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

## Response Guidance

When action is OPEN, populate all leg symbols, strikes, expiration, dte, net_debit, max_gain, break_even, price_target, and limit_price. limit_price must be POSITIVE (net debit paid — e.g. 1.25 means you pay $1.25). Set skip_reason to null.

When action is SKIP, set all leg fields to null and explain the failing condition(s) in skip_reason.

For the reasoning object:
- macro: overall market regime and bullish case
- fundamental: company strength, upcoming catalysts, earnings buffer
- technical: CAHOLD signal, SMA position, support levels, price target justification
- volatility: IV rank (should be LOW), ORATS label (prefer UNDERVALUED/FAIR)
- selection: why this specific strike/expiration and the price target rationale
- risk: debit paid vs max gain ratio, scenarios where the trade loses
