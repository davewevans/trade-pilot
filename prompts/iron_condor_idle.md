# Iron Condor — Entry Evaluation

You are evaluating whether to open an Iron Condor on {{underlying}}.

## Current Market Context
- Underlying price: {{underlying_price}}
- Confirmed market regime: {{confirmed_market_regime}}
- IV Rank: {{iv_rank}} ({{iv_environment}})
- VIX: {{vix}}
- Days to earnings: {{days_to_earnings}}
- IV overvaluation: {{iv_overvalued_label}}
- Contango label: {{contango_label}}
- Slope percentile: {{skew_percentile}}
- SPX trend: {{spx_trend}}
- Strategy routing hint: {{strategy_routing_hint}}

## Best Iron Condor Candidate
{{iron_condor_candidate}}

## Decision Rules
OPEN an iron condor ONLY IF ALL of the following are true:
1. confirmed_market_regime is NEUTRAL
2. iv_environment is HIGH (IVR >= {{iv_rank_min}} minimum)
3. VIX is between {{vix_min}} and {{vix_max}}
4. days_to_earnings > {{earnings_buffer_days}}
5. iron_condor_candidate.total_credit > {{min_total_credit}}
6. iron_condor_candidate.put_spread.liquidity_ok is true
7. iron_condor_candidate.call_spread.liquidity_ok is true
8. DTE is between {{dte_min}} and {{dte_max}}
9. Both short strike deltas are between {{put_short_delta_min}} and {{put_short_delta_max}}
10. iv_overvalued_label must NOT be "UNDERVALUED"
11. contango_label must NOT be "BACKWARDATION"
12. Prefer entry when skew_percentile is between 33 and 66 (balanced
    skew favors symmetrical iron condor)

SKIP if any condition fails.

## Required Response Format (JSON only):
```json
{
  "action": "OPEN" | "SKIP",
  "put_short_symbol": str,
  "put_long_symbol": str,
  "call_short_symbol": str,
  "call_long_symbol": str,
  "expiration": str,
  "dte": int,
  "total_credit": float,
  "max_loss": float,
  "limit_price": float,
  "reasoning": str,
  "skip_reason": str | null
}
```

IMPORTANT: limit_price must be NEGATIVE (net credit received). For example, -1.80 means you receive $1.80 credit.
