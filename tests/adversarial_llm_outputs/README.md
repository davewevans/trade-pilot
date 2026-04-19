# Adversarial LLM Output Test Corpus

This directory contains a fixed corpus of hand-crafted test fixtures that exercise every advisor path in `ai/claude_advisor.py` against pathological-but-possible Claude responses. Each fixture pins the expected behaviour of the parse / safe-skip / guardrail pipeline, serving as a regression baseline before model upgrades and as living documentation of known gaps.

## Background

`ClaudeAdvisor` uses Anthropic structured outputs (`output_config.format.json_schema`), so Claude is **physically incapable** of returning JSON that violates the registered schema. The real threat model is:

1. **Semantic violations that pass the schema** — wrong sign on limit_price, OCC symbol whose root doesn't match the analysed underlying, action/confidence contradictions, hallucinated reasoning prose.
2. **`stop_reason in ("refusal", "max_tokens")`** — the API terminates the response before producing output; the safe-skip fallback must fire.

Fixtures in the "known gap" category document cases where the current code does NOT catch the anomaly. These exist as regression baselines: the assertion is that the bot records the decision without crashing, and a `gap_note` explains what code change would close the gap.

## How to run

```bash
# Full corpus
pytest tests/adversarial_llm_outputs/

# Single fixture by id
pytest tests/adversarial_llm_outputs/ -k wrong_sign_credit__bull_put_spread__idle

# Verbose output
pytest tests/adversarial_llm_outputs/ -v
```

The corpus is included in the full test suite (`pytest tests/`) automatically.

## How to add a fixture

1. Copy an existing fixture from `fixtures/` as a starting point.
2. Update all fields — at minimum: `id`, `description`, `failure_mode`, `advisor`, `mocked_api`, `assertions.parse_layer`.
3. The `id` must match the file stem exactly (enforced at collection time).
4. Add `"known_gap": true` and a `"gap_note"` if the fixture documents a gap rather than a hard rejection.
5. Verify the (method, strategy_type, phase) triple maps to a real entry in `ai/schemas.py::_REGISTRY` — the loader raises at collection time if not.
6. Run `pytest tests/adversarial_llm_outputs/ -v` locally to confirm the new fixture is green.
7. Add a row to the index table below, keeping it sorted by `id`.

## How to retire a fixture

Delete the fixture file when any of the following apply:

- A guardrail has been added that closes the gap the fixture documents — the fixture's expected `should_validate` would now be wrong.
- The schema changed in a way that makes the failure mode unreachable (e.g. a field's `enum` now prevents a previously-valid value).
- The strategy itself has been removed.

Update the index table below when retiring.

## Fixture index

| id | failure_mode | known_gap |
|---|---|---|
| `action_confidence_contradiction__wheel__idle` | action_confidence_contradiction | yes |
| `debit_credit_reasoning_confusion__long_call_vertical__idle` | debit_credit_reasoning_confusion | yes |
| `hallucinated_reasoning__iron_condor__idle` | hallucinated_reasoning | yes |
| `occ_underlying_mismatch__wheel__idle` | occ_underlying_mismatch | yes |
| `stop_reason_max_tokens__iron_condor__idle` | stop_reason_max_tokens | no |
| `stop_reason_refusal__wheel__idle` | stop_reason_refusal | no |
| `wrong_sign_credit__bull_put_spread__idle` | semantic_sign_violation | no |
| `wrong_sign_debit__long_call_vertical__idle` | semantic_sign_violation | no |

## Pre-model-upgrade checklist

Run this procedure before flipping `advisor.model` to a new Claude version.

1. **Run the full corpus against the current model** (green = baseline confirmed):
   ```bash
   pytest tests/adversarial_llm_outputs/ -v
   ```

2. **Update `advisor.model`** in `ai/claude_advisor.py` to the candidate version.

3. **Run the corpus again** and diff the output. Any new failure is a regression introduced by the model upgrade.

4. **For each fixture that exercises a real API response** (i.e. not a stop_reason fixture), capture the actual `decision` dict returned by the advisor and compare it field-by-field against the previous run. Pay particular attention to:
   - `action` (did the model flip from OPEN to SKIP or vice versa?)
   - `confidence` level changes
   - `reasoning` prose changes that might indicate semantic drift

5. **Re-run the full test suite** to check for regressions in existing tests:
   ```bash
   pytest tests/
   ```

6. **Check the known-gap fixtures** — if a model upgrade happens to close a documented gap (i.e. the model now correctly self-corrects a sign violation or refuses a confidence=low trade), update the fixture's `known_gap` to `false`, flip the `should_validate` assertion, and add a row to the commit message explaining the change.

7. Only merge after all assertions in the corpus are green on the new model version.
