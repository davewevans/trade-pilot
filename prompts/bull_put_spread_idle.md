# Bull Put Spread — Entry Evaluation

You are evaluating whether to open a Bull Put Spread on {{underlying}}.

A bull put spread sells an OTM put (short) and buys a further OTM
put (long) at a lower strike. Both same expiration. You collect a
net credit. Max profit = credit if underlying stays above short put.
Max loss = wing width - credit if underlying falls below long put.

## Context
- Underlying price: {{underlying_price}}
- 50-day SMA trend: {{above_sma_50}}
- Confirmed market regime: {{confirmed_market_regime}}
- IV Rank: {{iv_rank}} ({{iv_environment}})
- Days to earnings: {{days_to_earnings}}
- Spread yield: {{spread_yield}}
- IV overvaluation: {{iv_overvalued_label}}
- Slope percentile: {{skew_percentile}}
- Strategy routing hint: {{strategy_routing_hint}}

## Best Bull Put Spread Candidate
{{best_candidate}}

## Decision Rules
OPEN if ALL of the following:
1. confirmed_market_regime is NEUTRAL or BULL
2. iv_rank >= 35
3. days_to_earnings > {{earnings_buffer_days}}
4. best_candidate.spread_yield >= 0.001 AND net_credit >= ${{min_net_credit}}
5. best_candidate.credit_to_width_ratio >= {{credit_to_width_min}}
6. best_candidate.liquidity_ok is true
7. DTE between {{dte_min}} and {{dte_max}}
8. Short put delta between -{{short_delta_min}} and -{{short_delta_max}}
9. Prefer entry when iv_overvalued_label is "OVERVALUED" or "FAIR"
   (ORATS forecast confirms options are overpriced). If
   iv_overvalued_label is "UNDERVALUED", note this as a risk factor
   and require extra-strong technicals to justify entry.
10. If skew_percentile > 66, note this as favorable — puts are
    expensive relative to history, increasing the edge for bull
    put spreads.

SKIP if any condition fails. Explain which condition(s) failed.

## Required Response Format (JSON only):
```json
{
  "action": "OPEN" | "SKIP",
  "short_put_symbol": str,
  "long_put_symbol": str,
  "expiration": str,
  "dte": int,
  "short_put_strike": float,
  "long_put_strike": float,
  "net_credit": float,
  "max_loss": float,
  "limit_price": float,
  "reasoning": str,
  "skip_reason": str | null
}
```

IMPORTANT: limit_price must be NEGATIVE (net credit received). For example, -0.85 means you receive $0.85 credit.
