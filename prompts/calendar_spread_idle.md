# Calendar Spread — Entry Evaluation

You are evaluating whether to open a Calendar Spread on {{underlying}}.

A calendar spread SELLS a short-term option and BUYS a longer-term option
at the SAME STRIKE. You pay a net debit. Profits from the short option
decaying faster (theta differential) and from contango (short-term IV <
long-term IV). This is a range-bound, time-decay strategy.

**Key risk:** Narrow profit zone centered around the strike. Any significant
directional move will lose money.

## Context
- Underlying price: {{underlying_price}}
- 50-day SMA: {{above_sma_50}}
- Bollinger position: {{bollinger_position}}
- Confirmed market regime: {{confirmed_market_regime}}
- IV Rank: {{iv_rank}} ({{iv_environment}})
- IV overvaluation: {{iv_overvalued_label}}
- Contango label: {{contango_label}}
- Contango value: {{contango_value}}
- Days to earnings: {{days_to_earnings}}
- Earnings date: {{next_earnings_date}}

## Best Calendar Candidate
{{calendar_candidate}}

## Decision Rules
OPEN if ALL of the following:
1. confirmed_market_regime is NEUTRAL
2. iv_environment is LOW or MODERATE (never HIGH — long leg is expensive)
3. contango_label is "NORMAL" (structural edge requires contango)
4. iv_overvalued_label is "FAIR" or "UNDERVALUED" (buying the long leg —
   want IV to be fairly priced or cheap)
5. Earnings date does NOT fall between short_expiration and long_expiration
6. Earnings date is NOT before short_expiration (both legs would be affected)
7. Stock is range-bound: above 50-SMA and within Bollinger Bands
8. calendar_candidate.net_debit <= $2.50
9. calendar_candidate.short_leg.open_interest >= 200
10. calendar_candidate.long_leg.open_interest >= 100
11. Strike is ATM (closest to 50 delta)
12. Short leg DTE: 20–35 days
13. Long leg DTE: 50–90 days (at least 30 days after short leg)

SKIP if any condition fails. Pay particular attention to the earnings
calendar — earnings between the two expirations creates an unpredictable
IV asymmetry that can destroy the calendar thesis.

## Required Response Format (JSON only):
```json
{
  "action": "OPEN" | "SKIP",
  "short_symbol": "str",
  "long_symbol": "str",
  "strike": 0.0,
  "short_expiration": "str",
  "long_expiration": "str",
  "short_dte": 0,
  "long_dte": 0,
  "net_debit": 0.0,
  "limit_price": 0.0,
  "reasoning": "str",
  "skip_reason": null
}
```

IMPORTANT: limit_price must be POSITIVE for debit spreads.
For example, 1.50 means you pay $1.50 debit per spread.
