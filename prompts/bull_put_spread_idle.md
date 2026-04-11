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
- Strategy routing hint: {{strategy_routing_hint}}

## Best Bull Put Spread Candidate
{{best_candidate}}

## Decision Rules
OPEN if ALL of the following:
1. confirmed_market_regime is NEUTRAL or BULL
2. iv_rank >= 35
3. days_to_earnings > 25
4. best_candidate.net_credit > 0.50
5. best_candidate.credit_to_width_ratio >= 0.15
6. best_candidate.liquidity_ok is true
7. DTE between 21 and 40
8. Short put delta between -0.20 and -0.30

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
