# Project Knowledge for Claude Code

Notes that future sessions should read before working on this codebase. Keep this file short — only add principles that are easy to violate by default and expensive to undo.

---

## Architectural principles

### 1. `ContextBuilder` is constructed per-strategy, never shared across accounts

`ContextBuilder.broker` is held on the instance and is account-bound. In a single-process multi-account job (e.g., `jobs/market_open.py`, which runs all six strategies in one Python process), sharing one ContextBuilder leaks the default broker's `account` / `positions` / `open_orders` / `portfolio_exposure` into every strategy's context.

The 2026-05-19 BAC leak (fix: `fix/context-builder-account-isolation`) was caused by one shared `ctx_builder`. Turnover Wheel (paper_6) read paper_1's open orders and saw Standard Wheel's BAC short put as its own "existing position."

**When you add a new strategy** to `market_open.py` (or any other multi-strategy entry point):

```python
my_strategy_ctx_builder = ContextBuilder(
    broker=my_strategy_broker,
    journal=journal,
    account_id="paper_N",   # the paper_N label matching AccountManager
)
```

Use the `paper_N` label (matches `AccountManager` and `portfolio_paper_N.json`), not the strategy name. Strategy name is the *what*; `account_id` is the *where*. They're 1:1 today but will diverge if a second account ever runs the same strategy.

When writing to the journal, include `account_id` in every `journal.append({...})` entry. When reading, pass `account_id` to `format_*_for_prompt` / `get_recent_*` — None means bot-wide (intentional only for `get_portfolio_patterns`); a paper_N label means strict equality and pre-fix entries (no `account_id` key) do not match.

Don't reach for a factory pattern to construct the builders. Repetition is more auditable than abstraction here — name each one explicitly (`wheel_ctx_builder`, `turnover_wheel_ctx_builder`, …) so the wiring is greppable.

See: `data/context_builder.py:146-180` (constructor docs), `jobs/market_open.py` ("Per-strategy ContextBuilders" block), and the `tests/test_context_builder_account_isolation.py` regression suite.
