# Persistence Contracts

This document defines which parts of trade-pilot's persisted state are authoritative history and which are derived views of broker truth. The distinction matters because reconciliation features (startup broker reconcile, drop-copy reconcile) must only overwrite one kind, never the other.

## The two categories

**Historical records** are append-only or timestamp-anchored facts about events that actually happened: an order was submitted at this time, it filled at that price, this decision was made with this reasoning. These cannot be re-derived from current broker state because the broker no longer knows (or never knew) the information. Historical records are written once and never rewritten. Reconciliation reads them; it never overwrites them.

**Derived state** represents "what is true right now" — open positions, cash balances, which wheel state each symbol is in, which spreads are live. Broker truth is the source; local copies exist for performance and for information the broker doesn't tag (like which strategy owns which position). Reconciliation is allowed — and required — to overwrite derived state when it diverges from the broker.

## Classification

| Store | Category | Reconcile overwrites? |
|---|---|---|
| SQLite `trades` | Historical | **Never** |
| SQLite `decisions` | Historical | **Never** |
| SQLite `cycles` | Historical | **Never** |
| SQLite `daily_summaries` | Historical | **Never** |
| `data/trade_journal.jsonl` | Historical (append-only) | **Never** |
| `data/snapshots/equity_history.json` | Historical | **Never** |
| `data/snapshots/regime_history.json` | Historical | **Never** |
| SQLite `strategy_states` | Derived | Yes |
| `data/snapshots/wheel_state.json` | Derived | Yes |
| `data/snapshots/turnover_wheel_state.json` | Derived | Yes |
| `data/snapshots/open_spreads.json` | Derived | Yes |
| `data/snapshots/portfolio*.json` | Derived (read-through cache) | Regenerated, not "overwritten" |
| `data/snapshots/circuit_breaker_state.json` | Computed state | Written by `CircuitBreaker.update()` only |

## Why `trades` is not reconcilable

A filled trade row carries fill_price, filled_at, submitted_at, limit_price, the decision_id that produced it, the cycle_id it belongs to, and a submission timestamp. Almost none of that is recoverable from the current position list at Alpaca. A position shows average cost and current market value — not the specific fill price of the order that created it, not when it filled, not the reasoning that led to it. If a reconciler tried to "fix" the trades table by synthesising rows from broker positions, it would invent history: the fill_price would be the average cost (wrong for any position that was scaled in), filled_at would be `now()` (false), decision_id would be null (breaking the link back to why the trade happened).

The trades table is the audit trail for every action the bot took. Destroying or rewriting it destroys the ability to analyze decision quality, compute realized P&L correctly, or diagnose a bad streak. If broker-visible positions don't match what trades says should exist, the correct response is to flag the mismatch to an operator — not to rewrite history to match a current observation that may itself be transient.

## What reconciliation is allowed to do

The startup broker reconcile and drop-copy reconcile jobs may, in enforce mode:

- Upsert rows in `strategy_states` to match the state derived from broker positions (via `derive_wheel_state_from_positions` and the spread reconstruction helper).
- Rewrite `wheel_state.json` and `turnover_wheel_state.json` to match broker-derived state for each tracked underlying.
- Update SpreadTracker (`open_spreads.json`) status transitions when a tracked spread is confirmed closed on the broker. They may **not** auto-register new spreads — an untracked spread found on the broker is an operator-review event, not an auto-heal event.
- Write their own report files (`startup_reconcile_report.json`, `drop_copy_last_report.json`) and the blocker flag (`drop_copy_block.json`).

They may not touch `trades`, `decisions`, `cycles`, `daily_summaries`, `trade_journal.jsonl`, `equity_history.json`, or `regime_history.json` under any mode.

## Boundary cases

**An expected trade row exists in `trades` as filled, but the position isn't on the broker.** Possibilities: the position was closed by NTA (assignment, expiry, called away) and the closing event wasn't recorded, the position was manually closed in the Alpaca UI, or there's a broker reporting lag. The reconciler updates `strategy_states` and the relevant state file to reflect IDLE. It does **not** mark the trade row as anything other than what it is (a filled entry). The cycle may need to be closed via a separate NTA recovery path, which is a different feature.

**A position exists on the broker with no corresponding filled trade row.** This is an "untracked_broker" event. In enforce mode at severity red, it writes HALTED.lock with reason `untracked_position`. Do not auto-create a trade row — the bot didn't place it, and synthesizing history to match the broker would hide whatever anomaly produced the position.

**A trade row is `pending` and the broker says it's filled.** This is not a contract violation. Pending-order reconciliation (`jobs/reconcile_orders.py` and `jobs/startup_reconciler.py`) resolves these by setting `fill_status='filled'`, `fill_price`, and `filled_at` from the broker's order record. That's a one-way transition on a row that was always expected to transition; it's not reconciliation overwriting historical fact.

## When to revisit this contract

This contract assumes the bot is the only writer against its accounts. When live trading adds a human in the loop — manual adjustments in the Alpaca UI, partial assignments handled by hand — some form of lightweight human-action reconciliation will be needed. That should take the shape of a separate manual-event log, not a relaxation of the append-only rule on `trades`.