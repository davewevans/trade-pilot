# Short Strangle — Entry Evaluation

⚠️ **UNDEFINED RISK STRATEGY** — There are no protective wings. A large
move in either direction creates theoretically unlimited loss. Be EXTRA
conservative with strike selection. When in doubt, SKIP.

You are evaluating whether to open a Short Strangle on {{underlying}}.

A short strangle sells an OTM put AND an OTM call on the same underlying
and expiration. You collect premium from both sides. Profitable when the
underlying stays between the short strikes and IV contracts. Unlike an
iron condor, there are NO protective wings.

## Context
- Underlying price: {{underlying_price}}
- Confirmed market regime: {{confirmed_market_regime}}
- IV Rank: {{iv_rank}} ({{iv_environment}})
- IV overvaluation: {{iv_overvalued_label}}
- Contango label: {{contango_label}}
- Premium richness: {{premium_richness_label}}
- Days to earnings: {{days_to_earnings}}
- Above 50-day SMA: {{above_sma_50}}
- Implied move: {{implied_move_pct}}%
- Strategy routing hint: {{strategy_routing_hint}}

## Best Short Strangle Candidate
{{strangle_candidate}}

## Decision Rules
OPEN ONLY IF ALL of the following are true:
1. confirmed_market_regime is NEUTRAL (never trending markets)
2. iv_environment is HIGH (IVR >= 50)
3. iv_overvalued_label is "OVERVALUED" or "FAIR" (never UNDERVALUED)
4. contango_label is NOT "BACKWARDATION"
5. days_to_earnings > 35 (undefined risk near earnings is reckless)
6. above_sma_50 is true (confirming range-bound behavior)
7. strangle_candidate.spread_yield >= 0.003 (combined credit >= 0.3% of stock)
8. Both legs: open_interest >= 200, bid_ask_spread_pct < 15%
9. Both short strikes are outside 1.5× implied move from current price
10. Short put delta between -0.15 and -0.20
11. Short call delta between 0.15 and 0.20
12. DTE between 30 and 50

SKIP if any condition fails. This is an undefined-risk strategy —
stricter criteria than credit spreads. Explain all passing and
failing conditions in your reasoning.

## Required Response Format (JSON only):
```json
{
  "action": "OPEN" | "SKIP",
  "put_symbol": "str",
  "call_symbol": "str",
  "expiration": "str",
  "dte": 0,
  "put_credit": 0.0,
  "call_credit": 0.0,
  "total_credit": 0.0,
  "limit_price": 0.0,
  "reasoning": "str",
  "skip_reason": null
}
```

IMPORTANT: limit_price must be NEGATIVE (net credit received).
For example, -2.50 means you receive $2.50 combined credit.
