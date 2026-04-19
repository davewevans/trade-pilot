# Decision Rubric v1.0.0

**Status:** Frozen  
**Effective date:** 2026-04-18  
**Applies to:** All strategy types tracked in the `decisions` table  

---

## Purpose

This rubric defines how individual Claude trade decisions are scored offline by the evaluation layer. Scores are written to the `decision_scores` table and reviewed monthly. The goal is to detect systematic drift from documented strategy rules and flag Claude decisions that are structurally incorrect (e.g., a SKIP citing `earnings_too_close` when earnings are 45 days away).

---

## Scoring Dimensions

Each dimension produces a score in [0.0, 1.0] for the applicable decision type.

### 5.1 `rule_adherence` (entry decisions only)

**Applies to:** Decisions where `action` ∈ {`SELL_PUT`, `SELL_CALL`, `OPEN`}  
**Skipped for:** `SKIP`, `HOLD`, `CLOSE`, `ROLL`

For each strategy rule listed below, the scorer checks whether the observable value satisfies the rule. A rule is **skipped** (excluded from the denominator) when the required value is not present in `context_json` or `reasoning`. Score = `rules_passed / rules_checked`.

#### Boundary rule (universal)
Values exactly at a threshold boundary are treated as a **pass**, not a fail.  
Examples: delta = 0.20 with `delta_min = 0.20` → pass; DTE = 21 with `dte_min = 21` → pass.

#### Rules by strategy type

**wheel — CSP (SELL_PUT)**
| Rule | Source field | Threshold |
|------|-------------|-----------|
| Delta in range | `reasoning.delta` or `context.selected_option.delta` | `delta_min` ≤ delta ≤ `delta_max` (default 0.20–0.30) |
| DTE in range | `reasoning.dte` or `context.selected_option.dte` | `dte_min` ≤ DTE ≤ `dte_max` (default 21–35) |
| IVR floor | `context.iv_rank` | ≥ `iv_rank_min` (default 30) |
| Earnings buffer | `context.earnings.days_until_earnings` or `context.fundamentals.days_to_earnings` | > `earnings_buffer_days` (default 21) |
| Open interest | `reasoning.open_interest` or `context.option_liquidity.open_interest` | ≥ `min_open_interest` (default 200) |

**wheel — CC (SELL_CALL)**
| Rule | Source field | Threshold |
|------|-------------|-----------|
| Delta in range | `reasoning.delta` | `delta_min` ≤ delta ≤ `delta_max` (default 0.20–0.35) |
| DTE in range | `reasoning.dte` | `dte_min` ≤ DTE ≤ `dte_max` (default 21–35) |
| Earnings buffer | same as CSP | > `earnings_buffer_days` (default 21) |
| Strike above cost basis | `context.cost_basis` and `reasoning.strike` | strike ≥ cost_basis |

**turnover_wheel** — same CSP rules as `wheel`, thresholds from `turnover_wheel.json`; CC has no delta cap (cost-basis only — `delta_min`/`delta_max` are absent from the CC block, so the `delta_range` rule is skipped for CC scoring).

**bull_put_spread / bear_call_spread (OPEN)**
| Rule | Source field | Threshold |
|------|-------------|-----------|
| Short delta in range | `reasoning.short_delta` or `reasoning.delta` | 0.20–0.30 |
| DTE in range | `reasoning.dte` | `dte_min`–`dte_max` (default 21–35) |
| Net credit floor | `reasoning.net_credit` | ≥ `min_net_credit` (default 0.50) |
| Earnings buffer | `context.fundamentals.days_to_earnings` | > `earnings_buffer_days` (default 21) |
| IV environment | `context.iv_environment` | in strategy `iv_environment.allowed` |

**long_call_vertical (OPEN)**
| Rule | Source field | Threshold |
|------|-------------|-----------|
| Long delta in range | `reasoning.long_delta` or `reasoning.delta` | 0.45–0.60 |
| DTE in range | `reasoning.dte` | `dte_min`–`dte_max` (default 30–60) |
| Net debit in range | `reasoning.net_debit` | `min_net_debit` ≤ debit ≤ `max_net_debit` (default 0.20–2.00) |
| Earnings buffer | `context.fundamentals.days_to_earnings` | > dte (earnings must clear expiry) |
| Market regime | `context.confirmed_market_regime` | must be `BULL` |
| IV environment | `context.iv_environment` | must be `LOW` |

**iron_condor / iron_butterfly (OPEN)**
| Rule | Source field | Threshold |
|------|-------------|-----------|
| Short delta in range | `reasoning.delta` | 0.15–0.25 |
| DTE in range | `reasoning.dte` | `dte_min`–`dte_max` |
| Total credit floor | `reasoning.total_credit` | ≥ `min_total_credit` |
| IVR floor | `context.iv_rank` | ≥ 50 |
| Earnings buffer | `context.fundamentals.days_to_earnings` | > `earnings_buffer_days` (default 30) |
| IV environment | `context.iv_environment` | must be `HIGH` |
| Market regime | `context.confirmed_market_regime` | must be `NEUTRAL` |

**calendar_spread (OPEN)**
| Rule | Source field | Threshold |
|------|-------------|-----------|
| DTE in range | `reasoning.short_dte` | `short_dte_min`–`short_dte_max` |
| Net debit in range | `reasoning.net_debit` | 0.50–3.00 |
| Earnings buffer | `context.fundamentals.days_to_earnings` | > `earnings_buffer_days` (default 45) |
| IV environment | `context.iv_environment` | in [`LOW`, `MODERATE`] |

---

### 5.3 `skip_validity_structural` (skip decisions only)

**Applies to:** Decisions where `action` = `SKIP`  
**Skipped for:** All other actions

Score is 0.0 (fail) or 1.0 (pass). Pass requires **both**:
1. `skip_reason_code` is a known value in `SkipCode.ALL`
2. The reason is contextually consistent:

| Reason code | Consistency check | Expected context indicator |
|-------------|------------------|--------------------------|
| `EARNINGS_TOO_CLOSE` | earnings data shows earnings within block window | `days_until_earnings` ≤ strategy's `earnings_buffer_days` (default 21) |
| `LOW_IVR` | IVR is actually low | `iv_rank` < strategy's `iv_rank_min` (default 30) |
| `HIGH_IVR` | IVR is actually high | `iv_rank` > 70 (approximate high threshold) |
| `IV_ENV_MISMATCH` | IV environment doesn't match strategy | `iv_environment` not in strategy's allowed list |
| `REGIME_MISMATCH` | Market regime doesn't match strategy | `confirmed_market_regime` not in strategy's allowed regimes |
| `CONFIDENCE_LOW` | Always structurally consistent (Claude-initiated, no context check required) | — |
| `OTHER` | Always structurally consistent (catch-all) | — |
| All remaining codes | Always structurally consistent when valid enum value | — |

If the context data required for a consistency check is absent, the check is treated as **passed** (benefit of the doubt).

---

## 6.1 Scoring output format

`ProgrammaticScorer.score_decision(decision)` returns a list of dicts, one per scored dimension:

```json
{
  "decision_id": 123,
  "dimension": "rule_adherence",
  "score": 0.8,
  "score_metadata": {
    "rules_checked": 5,
    "rules_passed": 4,
    "details": [
      {"rule": "ivr_floor", "passed": true, "observed": 42, "threshold": 30},
      {"rule": "earnings_buffer", "passed": false, "observed": 18, "threshold": 21},
      {"rule": "dte_range", "passed": true, "observed": 28, "threshold": "21–35"},
      {"rule": "delta_range", "passed": true, "observed": 0.25, "threshold": "0.20–0.30"},
      {"rule": "open_interest", "passed": true, "observed": 350, "threshold": 200}
    ],
    "skipped_rules": ["net_credit"]
  }
}
```

---

## Version history

| Version | Date | Notes |
|---------|------|-------|
| v1.0.0 | 2026-04-18 | Initial frozen rubric — programmatic dimensions only |
