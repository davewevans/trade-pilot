"""Covered-call guardrail: account isolation + fail-closed ownership fallback.

`Guardrails._check_sell_call` is the ONLY place `self.broker` is read. It is a
fallback that verifies >= 100 shares are owned when the passed-in positions
list is options-only (no equity rows). Two properties matter:

1. Account isolation — the fallback reads the broker the Guardrails instance was
   constructed with, so per-strategy Guardrails (wheel_broker=paper_2,
   turnover_wheel_broker=paper_6, ...) verify ownership against the SAME account
   that strategy executes against, never a shared default (paper_1).

2. Fail closed — a covered call is rejected whenever ownership cannot be
   positively confirmed against that account: broker is None, the broker lacks
   get_equity_positions, the call raises, or the shares simply aren't there.
   A safety check that can't confirm must refuse, not allow.

The primary (context-positions) ownership path must be unaffected: when the
shares are already present in the passed positions, the CC is allowed without
ever consulting the broker.

These are behavioral tests on guardrails.py. The per-strategy *construction*
wiring in jobs/market_open.py is asserted separately in
tests/test_wheel_execute_account_routing.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from strategies.guardrails import Guardrails

# Valid OCC call on AAPL; root extracts to "AAPL". Equity rows use bare "AAPL".
CC_SYMBOL = "AAPL260618C00150000"
ROOT = "AAPL"


def _sell_call_decision() -> dict:
    return {
        "action": "sell_call",
        "symbol": CC_SYMBOL,
        "qty": 1,
        "order_type": "limit",
        "limit_price": 1.50,
    }


def _validate(guardrails: Guardrails, positions: list) -> tuple[bool, str]:
    # account is ignored for sell_call; context=None skips earnings/ex-div.
    return guardrails.validate(_sell_call_decision(), account={}, positions=positions, context=None)


def _equity(symbol: str, qty: float) -> dict:
    return {"symbol": symbol, "qty": qty}


# ── Account isolation ──────────────────────────────────────────────────────


def test_fallback_reads_the_instances_own_broker_not_a_shared_default():
    """Same options-only decision, two Guardrails bound to two different
    accounts: the one whose account holds the shares allows the CC; the one
    whose account does not holds rejects it. Proves per-strategy construction
    isolates the ownership check by account."""
    owns = MagicMock()
    owns.get_equity_positions.return_value = [_equity(ROOT, 100)]
    lacks = MagicMock()
    lacks.get_equity_positions.return_value = []

    ok_owns, _ = _validate(Guardrails(broker=owns), positions=[])
    ok_lacks, reason_lacks = _validate(Guardrails(broker=lacks), positions=[])

    assert ok_owns is True, "Guardrails on the account that holds the shares must allow the CC"
    assert ok_lacks is False, "Guardrails on an account without the shares must reject the CC"
    assert "100 shares" in reason_lacks


# ── Fail closed ────────────────────────────────────────────────────────────


def test_fail_closed_when_broker_is_none():
    ok, reason = _validate(Guardrails(broker=None), positions=[])
    assert ok is False
    assert f"Must own >= 100 shares of {ROOT}" in reason


def test_fail_closed_when_broker_lacks_get_equity_positions():
    # SimpleNamespace has no get_equity_positions → hasattr() is False.
    broker = SimpleNamespace()
    ok, reason = _validate(Guardrails(broker=broker), positions=[])
    assert ok is False
    assert "100 shares" in reason


def test_fail_closed_when_get_equity_positions_raises():
    broker = MagicMock()
    broker.get_equity_positions.side_effect = RuntimeError("broker unreachable")
    ok, reason = _validate(Guardrails(broker=broker), positions=[])
    assert ok is False
    assert "100 shares" in reason


def test_fail_closed_when_shares_not_found_in_account():
    broker = MagicMock()
    broker.get_equity_positions.return_value = [_equity(ROOT, 50), _equity("MSFT", 100)]
    ok, reason = _validate(Guardrails(broker=broker), positions=[])
    assert ok is False, "fewer than 100 shares (and wrong ticker) must reject"
    assert "100 shares" in reason


def test_allowed_when_fallback_confirms_shares():
    broker = MagicMock()
    broker.get_equity_positions.return_value = [_equity(ROOT, 100)]
    ok, reason = _validate(Guardrails(broker=broker), positions=[])
    assert ok is True
    assert reason == ""


# ── Primary (context) ownership path is unaffected ─────────────────────────


def test_primary_context_ownership_does_not_consult_broker():
    """Shares present in the passed positions → CC allowed without touching the
    broker. The fallback exists only for options-only callers."""
    broker = MagicMock()
    ok, reason = _validate(Guardrails(broker=broker), positions=[_equity(ROOT, 100)])
    assert ok is True
    assert reason == ""
    broker.get_equity_positions.assert_not_called()


def test_fallback_skipped_when_positions_contain_other_equity():
    """If the positions list already contains equity rows (just not the CC's
    underlying), the fallback is intentionally skipped and the CC is rejected
    without consulting the broker — the context is treated as authoritative."""
    broker = MagicMock()
    broker.get_equity_positions.return_value = [_equity(ROOT, 100)]  # would allow if consulted
    ok, reason = _validate(Guardrails(broker=broker), positions=[_equity("MSFT", 100)])
    assert ok is False, "equity present but not the CC underlying → reject, no fallback"
    assert "100 shares" in reason
    broker.get_equity_positions.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
