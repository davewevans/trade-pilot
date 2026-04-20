# Trade-Pilot Knowledge Changelog

What was studied, what was learned, and what changed in the bot.
This file is the single source of truth for the knowledge pipeline.

## How This Works

1. Study courses, blogs, or ORATS documentation
2. Paste content into the Claude project (trade-pilot knowledge base)
3. Discuss with Claude — identify gaps, contradictions, improvements
4. Write Claude Code prompts to implement changes
5. Log the session here so we know what changed and why

Each entry records: sources consulted, key findings, and the specific
files that were modified. This makes it easy to trace why any rule
exists and revisit decisions later.

---

## [Unreleased]

### Changed
- `frontend/src/pages/HowBacktestingWorks.tsx` Section 7 rewritten. Header renamed
  from "How Backtests Feed Into Live Decisions" to "What the Backtester Powers". The
  four "Live"-badged cards that falsely claimed Claude's context includes `backtest_stats`,
  similar-trade outcomes, environment-match aggregates, and a backtest-vs-reality gap
  signal were replaced with (a) a single accurate card explaining the Sunday sweep writes
  to `symbol_strategy_stats` / `regime_strategy_stats` and that `pre_check_entry` consumes
  those via `BacktestStatsRepository.get_winrate_multiplier()` — not `context_builder` —
  and (b) a callout noting the Backtest Intel page UI is scaffolded but all three API
  endpoints (`/api/backtest/similar-trades`, `/api/backtest/environment-match`,
  `/api/backtest/reality-check`) have no backend implementation.
- `tests/integration/test_context_builder_truth_audit.py`: removed the three `xfail`
  parametrized cases (`backtest_stats`, `research.liquidity`, `research.winrate`). The
  dashboard claims they were guarding against no longer exist; the test cases are no longer
  applicable. All 20 remaining parametrized cases are unchanged.
- Design principle (R8): research layer data is gated upstream in `pre_check_entry` via
  multiplier/floor logic. Wiring the same data into Claude's prompt risks double-counting
  and overfitting to a noisy prior. Deterministic rules belong in code, not prompts.

### Added
- Wheel strategy now participates in liquidity scoring for new CSP and CC
  entries. `WheelStrategy` gains `STRATEGY_TYPE_MAP` and `liquidity_repo`
  constructor parameter. A new `evaluate_entry_liquidity()` method gates
  `IDLE → SHORT_PUT` entries via `wheel_csp` scores and `LONG_STOCK →
  SHORT_CALL` entries via `wheel_cc` scores. Management states
  (SHORT_PUT, SHORT_CALL) are never gated. Tier D triggers a hard SKIP
  before the Claude API call; Tiers A–C attach metadata only.
- `TradeRecorder.record_decision()` and `DecisionRepository.insert()`
  now accept a `research_metadata` kwarg that persists to the
  `decisions.research_metadata_json` column (column added in Phase 1 A1).
  Wheel decisions in `market_open.py` now pass `context["_research"]`
  through the recorder so the decisions table captures multiplier, tier,
  and confidence for every CSP/CC entry evaluation.
- Kill switch (`RESEARCH_SCORE_MULTIPLIER_ENABLED=false`) fully disables
  liquidity gating for the wheel the same way it does for spreads — Tier D
  is not skipped, metadata shows `confidence="disabled"`.

### Files changed
- `strategies/wheel_strategy.py` — `STRATEGY_TYPE_MAP`, `liquidity_repo` param,
  `evaluate_entry_liquidity()` method
- `jobs/market_open.py` — construct `LiquidityRepository` for wheel;
  call `evaluate_entry_liquidity` before `advisor.ask()`; pass
  `research_metadata` to all three `recorder.record_decision` call sites
- `database/recorder.py` — `research_metadata` kwarg on `record_decision`
- `database/repositories/decisions.py` — `research_metadata_json` in INSERT
- `tests/strategies/test_wheel_liquidity.py` — 10 new tests

---

## 2026-04-14 — ORATS University: Backtesting + Volatility Research

**Sources:** 17 ORATS University lessons covering backtesting
methodology, input parameters, measuring performance, custom
backtesting, strategy optimization, volatility surface, volatility
around earnings, predictive indicators, historical data, stock
scanning, option scanning, trade ideas and signals, placing orders,
and the review pillar.

**Key findings:**
- Bot was using raw mid price in backtester with no slippage model.
  ORATS calibrates slippage at 75% of bid-ask for 1-leg, 66% for
  2-leg, 53% for 4-leg based on 20 years of market-making data.
- ORATS IV forecast (orIvFcst20d) was available via API but not
  extracted or used. This tells you whether options are overpriced
  (good to sell) or underpriced (good to buy) — more valuable than
  raw IV rank alone.
- Spread yield (credit / stock price) is a better entry filter than
  a fixed dollar minimum. $0.75 on SPY ($530) vs IWM ($200) are
  fundamentally different setups.
- Contango (short-term vs long-term IV) is a regime signal the bot
  wasn't using. Backwardation = bearish, should block neutral
  strategies.
- Slope percentile was being extracted from ORATS but never
  referenced in prompts. High slope percentile = puts are expensive
  = better edge for selling put spreads.
- Ex-earnings IV is the "clean" volatility signal with earnings
  effect removed. Bot was using raw IV which is inflated near
  earnings.
- ORATS backtests by "spread yield" category and found that in low
  VIX environments, lower spread yields actually performed better.
- ORATS pre-computes 300M+ backtests and queries them at trade time.
  The bot's backtester was a manual research tool with no connection
  to live decisions.

**Changes made:**
- Backtesting engine: added ORATS slippage model, Sharpe ratio,
  VIX/SMA tracking per trade
- Backtest persistence: new SQLite tables (backtest_runs,
  backtest_trades) with similarity query support
- Weekly pre-computed backtests: Sunday job runs all strategy/symbol
  combos and caches results
- Environment matching: queries backtest DB filtered by current
  market conditions
- Similar trade matching: finds 5 nearest-neighbor historical trades
- Backtest vs reality: compares live performance to backtest
  predictions
- ORATS client: added IV forecast, ex-earnings IV, contango,
  slope forecast, forward ratios, confidence metrics
- Context builder: added iv_overvalued_label, contango_label,
  spread yield, and all new ORATS fields to Claude's context
- All credit strategy pre_checks: block entry when IV is UNDERVALUED
  per ORATS forecast
- Long call vertical: block entry when IV is OVERVALUED
- Iron condor: block entry during backwardation
- Bull put / bear call: replaced fixed $0.75 credit minimum with
  spread yield >= 0.1%
- New strategy: short strangle (inactive, not linked to account)
- New strategy: calendar spread (inactive, not linked to account)

**Prompt files changed:**
prompts/system.md (ORATS intelligence section, strategy updates),
prompts/bull_put_spread_idle.md, prompts/bear_call_spread_idle.md,
prompts/iron_condor_idle.md, prompts/long_call_vertical_idle.md,
prompts/wheel_idle.md, prompts/short_strangle_idle.md (new),
prompts/short_strangle_open.md (new), prompts/calendar_spread_idle.md
(new), prompts/calendar_spread_open.md (new)

**Code files changed:**
data/orats_client.py, data/context_builder.py,
strategies/bull_put_spread_strategy.py,
strategies/bear_call_spread_strategy.py,
strategies/iron_condor_strategy.py,
strategies/long_call_vertical_strategy.py,
strategies/short_strangle_strategy.py (new),
strategies/calendar_spread_strategy.py (new),
strategies/guardrails.py, backtesting/engine.py,
database/backtest_repository.py (new), database/db.py,
jobs/weekly_backtest.py (new), api/server.py

---

## 2026-04-12 — Schwab Education: Options Strategies + Blog Articles

**Sources:** 44 Schwab education articles and blog posts covering
aligning strategies with IV, IV percentile vs IV rank, theta decay
strategies, tools for trading around earnings, options expiration
mechanics, non-directional advanced spreads (iron condors, butterflies,
calendars, diagonals), directional spreads, vertical spread management,
assignment mechanics, and bid/ask spread execution.

**Key findings:**
- Iron condors benefit from falling IV, not just high IV — vega
  negative, so entering during IV contraction amplifies gains
- Non-directional strategies (iron condor, butterfly, calendar,
  diagonal) mapped to specific regime + IV environment combinations;
  documented in archive/non_directional_strategies.md
- Multi-account architecture validated: separate accounts per
  strategy type reduces correlation risk
- Directional spreads (long call vertical, long put vertical) need
  confirmed technical signals, not just regime classification
- Short put vertical is strictly superior to naked CSP for
  automation: defined max loss, lower capital requirement, same
  entry logic; documented in archive/directional_strategies.md
- Assignment is a designed state transition for the wheel, not a
  failure — but early assignment risk (ex-div, deep ITM) needs
  monitoring; documented in archive/assignment_mechanics.md
- Bid/ask spread width is a real execution cost that degrades
  expected yield; OI alone is insufficient as a liquidity filter;
  documented in archive/bid_ask_spreads_and_execution.md
- IV Rank and IV Percentile measure different things and can diverge
  significantly around IV spikes; bot uses IVR (range-based);
  documented in archive/iv_rank_and_strategy_alignment.md
- Theta decay is non-linear — 21–35 DTE is empirically the sweet
  spot; the 50% profit close is mathematically justified as it
  captures ~70% of theta income while avoiding gamma risk;
  documented in archive/theta_decay_and_the_wheel.md
- Post-earnings entry window (1–5 days after clean report) is a
  favorable CSP setup the bot wasn't explicitly recognizing;
  documented in archive/earnings_avoidance.md

**Changes made:**
- Designed and implemented StrategyRouter with regime + IV routing
- Built multi-account architecture (wheel, iron condor, default)
- Added iron condor, bull put spread, bear call spread, long call
  vertical strategies
- Created spread lifecycle state machine
- Added circuit breaker system

**Prompt files changed:** prompts/system.md (full strategy sections),
all spread idle/open prompt files created

**Code files changed:** strategies/*.py (all spread strategies),
strategies/strategy_router.py, strategies/circuit_breaker.py,
strategies/_spread_lifecycle.py, data/spread_tracker.py

---

## Pre-2026-04-12 — Foundation

**Sources:** Tastytrade courses, Options Alpha, personal trading
experience, Alpaca API documentation

**Key findings:**
- Wheel strategy fundamentals (CSP → assignment → CC → repeat)
- Greeks interpretation for option sellers
- IV rank as primary entry filter (>= 30)
- 21–35 DTE sweet spot for theta decay
- Earnings avoidance rules
- Technical analysis basics for strike selection

**Changes made:**
- Built entire trade-pilot system from scratch
- Wheel strategy with 4-phase state machine
- Claude as decision engine with guardrails layer
- Context builder assembling 15+ data sources
- ORATS integration for professional IV analytics
- Scheduler with 10 daily jobs
- SQLite database with dual-write from journal

**Prompt files changed:** All initial prompt files created

---

## Template for New Entries

Copy this when adding a new entry:

```
## YYYY-MM-DD — [Topic]

**Sources:** [What you studied]

**Key findings:**
- [Bullet points of what was learned]

**Changes made:**
- [What was actually changed in the codebase]

**Prompt files changed:** [List]

**Code files changed:** [List]
```
