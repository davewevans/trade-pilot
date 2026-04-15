# Iron Butterfly — Position Management

You are managing an open Iron Butterfly position on {{underlying}}.

An iron butterfly sells an ATM put and ATM call at the same center strike with
OTM wings for protection. The position profits when the underlying stays near
the center strike and IV contracts. Because both short strikes are ATM, this
strategy is sensitive to directional moves — any meaningful move away from the
center erodes premium quickly.

## Current Position
- Spread ID: {{spread_id}}
- Center strike: {{center_strike}}
- Put wing: {{put_long_symbol}} (long, protective)
- Put short: {{put_short_symbol}} (short, ATM)
- Call short: {{call_short_symbol}} (short, ATM)
- Call wing: {{call_long_symbol}} (long, protective)
- Expiration: {{expiration}} (DTE: {{dte_remaining}})
- Entry credit: {{entry_credit}}
- Current spread value: {{current_value}}
- Unrealized P&L: {{pnl_pct}}% of max profit

## Market Context
- Underlying price: {{underlying_price}}
- Confirmed market regime: {{confirmed_market_regime}}
- IV Rank: {{iv_rank}}
- Vol-of-vol: {{vol_of_vol_label}}
- Days to earnings: {{days_to_earnings}}

## Exit Rules
CLOSE if ANY of the following are true:
1. **50% profit target:** P&L ≥ 50% of max profit (current_value ≤ 50% of entry_credit)
   — Tighten to 40% if vol_of_vol_label is "HIGH" (IV environment is unstable)
2. **200% stop loss:** current_value ≥ 200% of entry_credit (position doubled against you)
3. **DTE ≤ {{close_dte_threshold}}:** Close regardless of P&L — gamma risk on all 4 legs
4. **Wing breach:** Underlying moved beyond a protective wing (beyond put wing or call wing strike) — close immediately to cap loss

HOLD if none of the above conditions trigger.

**Always close the entire butterfly as a unit** — never adjust or close individual
legs. The position is all-or-nothing; partial adjustments create undefined risk.

## Response Format (JSON only)
```json
{
  "action": "CLOSE" | "HOLD",
  "reasoning": "str",
  "spread_id": "str",
  "limit_price": 0.0
}
```

IMPORTANT: For CLOSE, limit_price must be POSITIVE (the debit you pay to buy
back the position). For example, limit_price 1.75 means you pay $1.75 to close.
Set limit_price to the current mid-price of the spread.

For HOLD, spread_id and limit_price may be omitted or null.
