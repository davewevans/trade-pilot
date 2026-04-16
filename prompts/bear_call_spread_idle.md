# Bear Call Spread — Entry Evaluation

You are evaluating whether to open a Bear Call Spread on {{underlying}}.

A bear call spread sells an OTM call (short) and buys a further OTM
call (long) at a higher strike. Both same expiration. You collect a
net credit. Profit if the underlying stays below the short call strike.

## Context
- Underlying price: {{underlying_price}}
- Confirmed market regime: {{confirmed_market_regime}}
- IV Rank: {{iv_rank}} ({{iv_environment}})
- Days to earnings: {{days_to_earnings}}
- Days to ex-dividend: {{days_to_ex_dividend}}
- RSI (14-day): {{rsi_14}}
- Above 50-day SMA: {{above_sma_50}}
- Above 200-day SMA: {{above_sma_200}}
- IV overvaluation: {{iv_overvalued_label}}
- Slope percentile: {{skew_percentile}}
- Strategy routing hint: {{strategy_routing_hint}}

## Best Bear Call Spread Candidate
{{best_candidate}}

## Decision Rules
OPEN if ALL of the following:
1. confirmed_market_regime is BEAR or NEUTRAL
2. iv_rank >= 40
3. days_to_earnings > {{earnings_buffer_days}}
4. days_to_ex_dividend > DTE (must avoid early assignment at ex-div)
5. best_candidate.spread_yield >= 0.001 AND net_credit >= ${{min_net_credit}}
6. best_candidate.credit_to_width_ratio >= {{credit_to_width_min}}
7. best_candidate.liquidity_ok is true
8. DTE between {{dte_min}} and {{dte_max}}
9. Short call delta between {{short_delta_min}} and {{short_delta_max}}
10. Bearish technical justification: underlying at resistance,
    below 50-day SMA, or RSI >= 60 (overbought)
11. Prefer entry when iv_overvalued_label is "OVERVALUED". Hard skip
    if iv_overvalued_label is "UNDERVALUED" — options are cheap,
    poor edge for selling calls.
12. If skew_percentile < 33, calls are relatively expensive compared
    to puts → favorable for selling call spreads.

SKIP if underlying is in a strong uptrend (above both SMAs with
RSI < 60 and no resistance nearby) — this is a bearish strategy
and needs a bearish or neutral technical setup.

## Response Guidance

When action is OPEN, populate all leg symbols, strikes, expiration, dte, net_credit, max_loss, and limit_price. limit_price must be NEGATIVE (net credit received — e.g. -0.75 means $0.75 credit). Set skip_reason to null.

When action is SKIP, set all leg fields to null and explain the failing condition(s) in skip_reason.

For the reasoning object:
- macro: overall market environment and regime
- fundamental: company factors, earnings, ex-dividend date
- technical: price relative to SMAs, RSI, resistance level — include your bearish rationale here
- volatility: IV rank, ORATS signals, call skew (skew_percentile < 33 is favorable)
- selection: why this strike/expiration was chosen (or why none qualified)
- risk: max loss, key upside risks if underlying rallies
