"""Integration test: macro block fires before Claude is called in market_open.

Verifies that when is_blocked() returns True:
  - Claude is never invoked
  - market_open.run() returns early
  - The report section contains "MACRO BLOCK"
"""

from unittest.mock import MagicMock, patch

import pytest


def _make_cb_mocks():
    mock_cb_status = MagicMock()
    mock_cb_status.status = "GREEN"
    mock_cb_status.daily_pnl_pct = 0.0
    mock_cb_status.drawdown_pct = 0.0

    mock_cb = MagicMock()
    mock_cb.update.return_value = mock_cb_status
    mock_cb.is_halted.return_value = False
    mock_cb.get_position_size_multiplier.return_value = 1.0
    mock_cb._status = mock_cb_status
    return mock_cb, mock_cb_status


def _make_broker_mock():
    mock_broker = MagicMock()
    mock_broker.get_clock.return_value = {"is_open": True}
    mock_broker.get_account.return_value = {"portfolio_value": "100000", "equity": "100000"}
    return mock_broker


def _run_with_macro_blocked(reason="MACRO_EVENT_PROXIMITY: FOMC on 2026-05-07 at 14:00 ET"):
    """Run market_open.run() with macro block active. Returns (claude_mock, append_calls)."""
    mock_broker = _make_broker_mock()
    mock_cb, _ = _make_cb_mocks()
    mock_claude = MagicMock()
    append_calls = []

    with (
        patch("brokers.broker_factory.get_broker", return_value=mock_broker),
        patch("brokers.broker_factory.make_broker", return_value=mock_broker),
        patch("strategies.circuit_breaker.CircuitBreaker", return_value=mock_cb),
        patch(
            "strategies.circuit_breaker.CircuitBreaker.calculate_portfolio_equity",
            return_value=100000.0,
        ),
        patch("data.state_writer.StateWriter"),
        patch("ai.claude_advisor.ClaudeAdvisor", return_value=mock_claude),
        patch("data.macro_calendar.is_blocked", return_value=(True, reason)),
        patch("data.macro_calendar.fomc_coverage_days", return_value=90),
        patch("data.source_health.SourceHealth"),
        # append_section is imported at module level in market_open.py, patch there
        patch(
            "jobs.market_open.append_section",
            side_effect=lambda title, body: append_calls.append((title, body)),
        ),
    ):
        from jobs import market_open as _mo
        _mo.run()

    return mock_claude, append_calls


class TestMacroBlockIntegration:
    def test_claude_not_called_when_macro_blocked(self):
        mock_claude, _ = _run_with_macro_blocked()
        mock_claude.get_decision.assert_not_called()
        mock_claude.assert_not_called()

    def test_report_section_contains_macro_block(self):
        _, append_calls = _run_with_macro_blocked()
        bodies = [body for _, body in append_calls]
        assert any("MACRO BLOCK" in body for body in bodies), (
            f"Expected 'MACRO BLOCK' in report section. Got: {bodies}"
        )

    def test_reason_appears_in_report(self):
        reason = "MACRO_EVENT_PROXIMITY: FOMC on 2026-05-07 at 14:00 ET"
        _, append_calls = _run_with_macro_blocked(reason=reason)
        all_text = " ".join(body for _, body in append_calls)
        assert "FOMC" in all_text

    def test_block_bypassed_when_disabled(self):
        """When MACRO_EVENT_BLOCK_ENABLED=False, is_blocked is never called."""
        mock_broker = _make_broker_mock()
        mock_cb, _ = _make_cb_mocks()
        is_blocked_mock = MagicMock(return_value=(True, "would block if enabled"))

        with (
            patch("brokers.broker_factory.get_broker", return_value=mock_broker),
            patch("brokers.broker_factory.make_broker", return_value=mock_broker),
            patch("strategies.circuit_breaker.CircuitBreaker", return_value=mock_cb),
            patch(
                "strategies.circuit_breaker.CircuitBreaker.calculate_portfolio_equity",
                return_value=100000.0,
            ),
            patch("data.state_writer.StateWriter"),
            patch("data.macro_calendar.is_blocked", is_blocked_mock),
            patch("jobs.market_open.append_section"),
            # Patch downstream to prevent errors after the macro check passes
            patch("strategies.strategy_router.StrategyRouter"),
            patch("data.trade_journal.TradeJournal"),
            patch("data.context_builder.ContextBuilder"),
            patch("data.spread_tracker.SpreadTracker"),
            patch("ai.claude_advisor.ClaudeAdvisor"),
            patch("strategies.guardrails.Guardrails"),
            patch("jobs.market_open.settings") as mock_settings,
        ):
            mock_settings.MACRO_EVENT_BLOCK_ENABLED = False
            mock_settings.TIMEZONE = "America/New_York"

            from jobs import market_open as _mo
            try:
                _mo.run()
            except Exception:
                pass  # downstream errors expected without full environment

        # The key assertion: is_blocked was NOT called (the block is disabled)
        is_blocked_mock.assert_not_called()
