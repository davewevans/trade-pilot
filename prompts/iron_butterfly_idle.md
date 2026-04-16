# Iron Butterfly — Entry Evaluation

You are evaluating whether to open a Short Iron Butterfly on {{underlying}}.

An iron butterfly sells an ATM put and an ATM call **at the same center strike**,
then buys an OTM put wing below and an OTM call wing above for protection. All
four legs are on the same expiration. It is a defined-risk credit strategy —
maximum profit is achieved when the underlying closes exactly at the center strike
at expiration.

Compared to the iron condor:
- **Iron condor:** short strikes are OTM → wider profit zone, lower premium
- **Iron butterfly:** short strikes are ATM → narrower profit zone, higher premium

Use the butterfly when you have high conviction the underlying will stay very
close to a specific price. It demands more precision than the condor.

## Context
- Underlying price: {{underlying_price}}
- Confirmed market regime: {{confirmed_market_regime}}
- IV Rank: {{iv_rank}} ({{iv_environment}})
- IV overvaluation: {{iv_overvalued_label}}
- Contango label: {{contango_label}}
- Premium richness: {{premium_richness_label}}
- Days to earnings: {{days_to_earnings}}
- Implied move: {{implied_move_pct}}%
- Strategy routing hint: {{strategy_routing_hint}}

## Best Iron Butterfly Candidate
{{iron_butterfly_candidate}}

## Entry Rules
OPEN ONLY IF ALL of the following are true:
1. confirmed_market_regime is NEUTRAL
2. iv_environment is HIGH (IVR ≥ {{iv_rank_min}})
3. iv_overvalued_label is "OVERVALUED" or "FAIR" (never UNDERVALUED)
4. contango_label is NOT "BACKWARDATION"
5. days_to_earnings > {{earnings_buffer_days}}
6. DTE between {{dte_min}} and {{dte_max}}
7. total_credit ≥ {{min_total_credit}} (butterfly collects more premium than IC)
8. credit-to-width ratio ≥ 30% (total_credit / wing_width ≥ 0.30)
9. Both short legs at the SAME center strike (ATM, closest to current price)
10. Wing width symmetric: put wing width = {{put_wing_width}}, call wing width = {{call_wing_width}}
11. Both wings: open_interest ≥ 200, bid-ask spread < 15%
12. At most {{max_concurrent_positions}} open butterfly positions total

SKIP if any condition fails. Explain all passing and failing conditions in your
reasoning. Be especially explicit about whether the short strikes are ATM and
whether they match.

## Response Guidance

When action is OPEN, populate all four leg symbols, expiration, dte, total_credit, max_loss, limit_price, and center_strike. limit_price must be NEGATIVE (credit received — e.g. -3.50 means $3.50 credit). The put_short_symbol and call_short_symbol MUST have the same strike price (the center/ATM strike). The put_long_symbol strike must be below center and the call_long_symbol strike must be above center. Set skip_reason to null.

When action is SKIP, set all leg fields to null and explain the failing condition(s) in skip_reason.

For the reasoning object:
- macro: market regime and neutrality assessment
- fundamental: earnings proximity, catalyst risks
- technical: price proximity to center strike, expected range
- volatility: IV rank, overvaluation label, contango status, premium richness
- selection: why this center strike and wing width were chosen (or why none qualified)
- risk: max loss relative to credit, tight profit zone, key risk scenarios
