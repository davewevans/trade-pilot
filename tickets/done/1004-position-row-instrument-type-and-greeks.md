# Position-row instrument type + equity Greeks (`1004-position-row-instrument-type-and-greeks`)

**Status:** done
**Synthesized:** 2026-07-03

## Purpose

The account position table showed a blank Type pill and "—" Delta/Theta cells for equity
positions, even though the Portfolio Greeks aggregate correctly computed Net Delta for the same
shares (e.g. paper_1's VZ position: row blank, aggregate Net Delta −100.00). Root cause was in the
snapshot writer, not the UI: `StateWriter.write_portfolio_snapshot` emitted no `instrument_type`
field and left equity `delta` as `None`. This ticket adds `instrument_type` per row and computes a
signed-share-count equity delta using the exact sign convention `compute_portfolio_greeks` already
uses, so the row and the aggregate can never disagree again.

## What was done

- `data/state_writer.py` — added `_classify_instrument_type(p)`, which checks
  `p.get("option_type")` first, then falls back to parsing the OCC symbol via `utils.occ.parse_occ`
  (real Alpaca `Position.model_dump()` payloads carry no `strike_price`/`expiration_date` fields —
  only the OCC symbol reliably distinguishes options from equities in production). Every emitted
  row now carries `instrument_type` (never blank, defaults to `"stock"`). When
  `instrument_type == "stock"` and the broker's `delta` is `None`, the row's `delta` is set to the
  signed share count (`+qty` long / `-qty` short), matching `compute_portfolio_greeks`'s documented
  sign convention exactly (`data/context_builder.py:2401-2408`). `theta`/`vega`/`gamma` untouched.
- `frontend/src/types/index.ts` — added optional `instrument_type` to the `Position` type.
- `frontend/src/pages/AccountDetail.tsx` — `PositionRow` maps `instrument_type` to a
  Stock/Call/Put Type pill, falling back to the prior `strategy_type` display when the field is
  absent (older snapshots). Stock rows show theta as `0.00` instead of `—`; delta needed no change
  since the row already rendered any numeric `p.delta`, now populated by the backend.
- `tests/test_state_writer.py` — added `TestInstrumentTypeAndEquityDelta`: an equity position
  (`side="short"`, `qty=100`, `delta=None`) asserts `instrument_type == "stock"` and `delta == -100`;
  put/call option legs assert `instrument_type` is `"put"`/`"call"` with explicit deltas preserved.

## Key files

- `data/state_writer.py` — `_classify_instrument_type`, `write_portfolio_snapshot` position loop.
- `data/context_builder.py:2401-2408` — `compute_portfolio_greeks` sign convention (read-only
  reference; zero diff, confirmed via `git diff --stat`).
- `frontend/src/types/index.ts`, `frontend/src/pages/AccountDetail.tsx` — `instrument_type` render.
- `tests/test_state_writer.py` — new coverage.

## Notes for future work

Gate: `pytest tests/test_state_writer.py tests/test_portfolio_greeks.py -q` — 35 passed. Also
spot-checked `tests/test_context_builder_account_isolation.py` and `tests/test_occ.py` for
regressions (47 passed, none introduced). `cd frontend && npm run build` passes clean. Per the
ticket's "Post-merge (user)" note: on paper_1's account page, confirm the VZ row now shows
Type = "Stock" and Delta = −100 (matching Portfolio Greeks), and that option rows on other
accounts still show Put/Call + their option Greeks — this visual check is post-merge, not a
runner gate.
