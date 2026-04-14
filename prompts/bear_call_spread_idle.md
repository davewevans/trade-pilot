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
3. days_to_earnings > 25
4. days_to_ex_dividend > DTE (must avoid early assignment at ex-div)
5. best_candidate.spread_yield >= 0.001 AND net_credit >= $0.30
6. best_candidate.credit_to_width_ratio >= 0.15
7. best_candidate.liquidity_ok is true
8. DTE between 21 and 40
9. Short call delta between 0.20 and 0.30
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

## Required Response Format (JSON only):
```json
{
  "action": "OPEN" | "SKIP",
  "short_call_symbol": str,
  "long_call_symbol": str,
  "expiration": str,
  "dte": int,
  "short_call_strike": float,
  "long_call_strike": float,
  "net_credit": float,
  "max_loss": float,
  "limit_price": float,
  "bearish_rationale": str,
  "reasoning": str,
  "skip_reason": str | null
}
```

IMPORTANT: limit_price must be NEGATIVE (net credit received). For example, -0.75 means you receive $0.75 credit.
