"""Tests for the Turnover Wheel end-to-end wiring.

Covers:
- ContextBuilder.build(strategy_name="turnover_wheel") parameterization
- TurnoverWheelStrategy.underlying_price_at_entry persistence
- StrategyRouter TURNOVER_WHEEL_ENABLED flag
- ClaudeAdvisor.ask_turnover_wheel() — prompt content, schema, safe-SKIP
- get_schema("turnover_wheel", phase) is same object as get_schema("wheel", phase)
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai.schemas import get_schema
from strategies.turnover_wheel_strategy import TurnoverWheelState

# Some imports require pydantic_core or numpy compiled for the current Python
# version. On Python 3.14 the venv's compiled extensions target 3.13 and
# fail to load. We detect this at collection time and skip those tests.
try:
    from data.context_builder import ContextBuilder as _CBImport
    _CONTEXT_BUILDER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _CONTEXT_BUILDER_AVAILABLE = False

try:
    from ai.claude_advisor import ClaudeAdvisor as _CAImport
    _CLAUDE_ADVISOR_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _CLAUDE_ADVISOR_AVAILABLE = False

_skip_no_cb = pytest.mark.skipif(
    not _CONTEXT_BUILDER_AVAILABLE,
    reason="ContextBuilder unavailable (pydantic_core not compiled for this Python version)",
)

_skip_no_ca = pytest.mark.skipif(
    not _CLAUDE_ADVISOR_AVAILABLE,
    reason="ClaudeAdvisor unavailable (pydantic_core/numpy not compiled for this Python version)",
)


# ── Schema identity tests ─────────────────────────────────────────────────────

class TestTurnoverWheelSchemas:
    """Turnover Wheel schemas must be the SAME object as Wheel schemas."""

    def test_idle_schema_is_wheel_idle(self):
        assert get_schema("turnover_wheel", "idle") is get_schema("wheel", "idle")

    def test_short_put_schema_is_wheel_short_put(self):
        assert get_schema("turnover_wheel", "short_put") is get_schema("wheel", "short_put")

    def test_long_stock_schema_is_wheel_long_stock(self):
        assert get_schema("turnover_wheel", "long_stock") is get_schema("wheel", "long_stock")

    def test_short_call_schema_is_wheel_short_call(self):
        assert get_schema("turnover_wheel", "short_call") is get_schema("wheel", "short_call")

    def test_all_four_phases_accessible(self):
        for phase in ("idle", "short_put", "long_stock", "short_call"):
            schema = get_schema("turnover_wheel", phase)
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False


# ── Router flag tests ─────────────────────────────────────────────────────────

class TestTurnoverWheelRouterFlag:
    """TURNOVER_WHEEL_ENABLED flag controls router inclusion."""

    def _make_router(self):
        from strategies.strategy_router import StrategyRouter
        return StrategyRouter()

    def _context(self):
        return {
            "confirmed_market_regime": "NEUTRAL",
            "iv_environment": "MODERATE",
            "iv_rank": 55,
            "support_bounce_signal": {"cahold_detected": False},
        }

    def test_turnover_wheel_included_when_enabled(self):
        router = self._make_router()
        with patch("strategies.strategy_router.settings") as mock_settings:
            mock_settings.TURNOVER_WHEEL_ENABLED = True
            active = router.get_active_strategies(
                self._context(), {}, circuit_breaker_status="GREEN"
            )
        assert "turnover_wheel" in active

    def test_turnover_wheel_excluded_when_disabled(self):
        router = self._make_router()
        with patch("strategies.strategy_router.settings") as mock_settings:
            mock_settings.TURNOVER_WHEEL_ENABLED = False
            active = router.get_active_strategies(
                self._context(), {}, circuit_breaker_status="GREEN"
            )
        assert "turnover_wheel" not in active

    def test_wheel_always_included_regardless_of_flag(self):
        router = self._make_router()
        with patch("strategies.strategy_router.settings") as mock_settings:
            mock_settings.TURNOVER_WHEEL_ENABLED = False
            active = router.get_active_strategies(
                self._context(), {}, circuit_breaker_status="GREEN"
            )
        assert "wheel" in active


# ── ContextBuilder parameterization tests ────────────────────────────────────

@_skip_no_cb
class TestContextBuilderStrategyName:
    """ContextBuilder.build() strategy_name param selects correct state file."""

    def _make_builder(self):
        from data.context_builder import ContextBuilder
        broker = MagicMock()
        broker.get_account.return_value = {}
        broker.get_positions.return_value = []
        broker.get_orders.return_value = []
        journal = MagicMock()
        journal.format_for_prompt.return_value = None
        journal.format_stats_for_prompt.return_value = None
        journal.format_skip_history_for_prompt.return_value = None
        journal.format_guardrail_rejections_for_prompt.return_value = None
        journal.format_portfolio_patterns_for_prompt.return_value = None
        builder = ContextBuilder(broker=broker, journal=journal)
        return builder

    def _heavy_mock_build(self, builder, symbol="AAPL", wheel_state="LONG_STOCK",
                          strategy_name="wheel"):
        """Call build() with all heavy external calls mocked out."""
        with patch("data.context_builder.market_data") as mock_md, \
             patch("data.context_builder.ThreadPoolExecutor") as mock_pool, \
             patch("data.context_builder._fetch_news", return_value=None), \
             patch("data.context_builder.ContextBuilder._fetch_account", return_value={}), \
             patch("data.context_builder.ContextBuilder._fetch_positions", return_value=[]), \
             patch("data.context_builder.ContextBuilder._fetch_orders", return_value=[]), \
             patch("data.context_builder.ContextBuilder._fetch_option_chain", return_value=[]), \
             patch("data.context_builder.RegimeStabilityFilter"):
            # Make thread pool return Nones for all futures
            mock_future = MagicMock()
            mock_future.result.return_value = None
            mock_executor = MagicMock()
            mock_executor.__enter__ = MagicMock(return_value=mock_executor)
            mock_executor.__exit__ = MagicMock(return_value=False)
            mock_executor.submit.return_value = mock_future
            mock_pool.return_value = mock_executor

            mock_md.get_stock_technicals.return_value = {"current_price": 150.0}

            return builder.build(symbol, wheel_state, strategy_name=strategy_name)

    def test_default_strategy_name_is_wheel(self):
        """build(symbol, state) without strategy_name defaults to wheel."""
        builder = self._make_builder()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({
                "cost_basis": 145.0,
                "total_premium_collected": 5.0,
                "roll_count": 1,
            }, f)
            tmp_path = f.name
        try:
            with patch("strategies.wheel_strategy.STATE_FILE", tmp_path):
                ctx = self._heavy_mock_build(builder, wheel_state="LONG_STOCK",
                                              strategy_name="wheel")
            assert "wheel_cost_basis" in ctx
            assert ctx["wheel_cost_basis"]["effective_cost_basis"] == 145.0
        finally:
            os.unlink(tmp_path)

    def test_turnover_wheel_reads_turnover_state_file(self):
        """build(strategy_name='turnover_wheel') reads turnover_wheel_state.json."""
        builder = self._make_builder()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({
                "cost_basis": 200.0,
                "total_premium_collected": 8.5,
                "roll_count": 2,
            }, f)
            tmp_path = f.name
        try:
            with patch("strategies.turnover_wheel_strategy.STATE_FILE", tmp_path):
                ctx = self._heavy_mock_build(
                    builder, wheel_state="LONG_STOCK",
                    strategy_name="turnover_wheel"
                )
            assert "wheel_cost_basis" in ctx
            assert ctx["wheel_cost_basis"]["effective_cost_basis"] == 200.0
            assert ctx["wheel_cost_basis"]["total_premium_collected"] == 8.5
            assert ctx["wheel_cost_basis"]["roll_count"] == 2
        finally:
            os.unlink(tmp_path)

    def test_turnover_wheel_surfaces_underlying_price_at_entry(self):
        """underlying_price_at_entry from state file is surfaced as top-level key."""
        builder = self._make_builder()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({
                "cost_basis": 195.0,
                "total_premium_collected": 5.0,
                "roll_count": 0,
                "underlying_price_at_entry": 198.50,
            }, f)
            tmp_path = f.name
        try:
            with patch("strategies.turnover_wheel_strategy.STATE_FILE", tmp_path):
                ctx = self._heavy_mock_build(
                    builder, wheel_state="SHORT_PUT",
                    strategy_name="turnover_wheel"
                )
            assert "underlying_price_at_entry" in ctx
            assert ctx["underlying_price_at_entry"] == 198.50
        finally:
            os.unlink(tmp_path)

    def test_turnover_wheel_no_underlying_price_no_crash(self):
        """State file without underlying_price_at_entry loads without crash."""
        builder = self._make_builder()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({
                "cost_basis": 195.0,
                "total_premium_collected": 5.0,
                "roll_count": 0,
                # No underlying_price_at_entry field
            }, f)
            tmp_path = f.name
        try:
            with patch("strategies.turnover_wheel_strategy.STATE_FILE", tmp_path):
                ctx = self._heavy_mock_build(
                    builder, wheel_state="LONG_STOCK",
                    strategy_name="turnover_wheel"
                )
            # Should load cleanly and either not have the key or have None
            upae = ctx.get("underlying_price_at_entry")
            assert upae is None  # missing field → None
        finally:
            os.unlink(tmp_path)

    def test_unknown_strategy_name_skips_cost_basis_no_crash(self):
        """An unknown strategy_name logs warning and skips cost-basis, no crash."""
        builder = self._make_builder()
        # Should not crash
        ctx = self._heavy_mock_build(
            builder, wheel_state="LONG_STOCK",
            strategy_name="unknown_strategy"
        )
        # wheel_cost_basis should not be injected
        assert ctx.get("wheel_cost_basis") is None


# ── TurnoverWheelStrategy state persistence tests ────────────────────────────

class TestTurnoverWheelStatePersistence:
    """underlying_price_at_entry is persisted, not overwritten on rolls, cleared on IDLE."""

    def _make_strategy(self, state_path: str):
        """Create TurnoverWheelStrategy backed by a temp state file."""
        from strategies.turnover_wheel_strategy import TurnoverWheelStrategy
        broker = MagicMock()
        broker.get_positions.return_value = []
        broker.get_position.side_effect = Exception("no position")
        with patch("strategies.turnover_wheel_strategy.STATE_FILE", state_path), \
             patch("strategies.turnover_wheel_strategy._LEGACY_STATE_FILE", Path(state_path + ".legacy")):
            strategy = TurnoverWheelStrategy(broker)
        return strategy

    def test_underlying_price_at_entry_defaults_none(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        os.unlink(tmp)  # doesn't exist yet — fresh start
        try:
            strategy = self._make_strategy(tmp)
            assert strategy.underlying_price_at_entry is None
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_save_and_load_underlying_price_at_entry(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({}, f)
            tmp = f.name
        try:
            strategy = self._make_strategy(tmp)
            strategy.underlying_price_at_entry = 155.75
            with patch("strategies.turnover_wheel_strategy.STATE_FILE", tmp):
                strategy.save_state()
            # Reload
            strategy2 = self._make_strategy(tmp)
            assert strategy2.underlying_price_at_entry == 155.75
        finally:
            os.unlink(tmp)

    def test_backward_compat_missing_field_loads_as_none(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({
                "symbol": "AAPL",
                "state": "SHORT_PUT",
                "cost_basis": None,
                "total_premium_collected": 2.5,
                "roll_count": 0,
            }, f)
            tmp = f.name
        try:
            strategy = self._make_strategy(tmp)
            assert strategy.underlying_price_at_entry is None
        finally:
            os.unlink(tmp)

    def test_idle_reset_clears_underlying_price_at_entry(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({}, f)
            tmp = f.name
        try:
            strategy = self._make_strategy(tmp)
            strategy.underlying_price_at_entry = 145.0
            strategy.total_premium_collected = 3.0
            strategy.roll_count = 1
            # Simulate IDLE reset (no positions found)
            with patch("strategies.turnover_wheel_strategy.STATE_FILE", tmp):
                strategy.broker.get_positions.return_value = []
                strategy.broker.get_position.side_effect = Exception("no position")
                strategy.get_current_state("AAPL")
            assert strategy.underlying_price_at_entry is None
            assert strategy.total_premium_collected == 0.0
            assert strategy.roll_count == 0
        finally:
            os.unlink(tmp)


# ── ClaudeAdvisor.ask_turnover_wheel() tests ─────────────────────────────────

def _make_advisor():
    """Construct a ClaudeAdvisor without hitting Anthropic or reading prompts."""
    if not _CLAUDE_ADVISOR_AVAILABLE:
        pytest.skip("ClaudeAdvisor unavailable")
    from ai.claude_advisor import ClaudeAdvisor
    with patch("ai.claude_advisor.anthropic.Anthropic"), \
         patch("ai.claude_advisor.settings"):
        advisor = ClaudeAdvisor.__new__(ClaudeAdvisor)
        advisor.client = MagicMock()
        advisor.model = "claude-sonnet-4-6"
        advisor._last_usage = None
        advisor._api_usage_repo = None
        advisor.thinking_mode = "off"
        advisor.system_prompt = "SYSTEM PROMPT"
        advisor.prompt_version = "test"
        from strategies.wheel_strategy import WheelState
        advisor.phase_prompts = {s: f"wheel_prompt_{s.value}" for s in WheelState}
        # Distinctive phrase from turnover_wheel_idle.md:
        # "There is NO delta cap on the Turnover Wheel covered call"
        # (in long_stock) and "**Strategy: Turnover Wheel**" (in all TW prompts)
        advisor.turnover_wheel_phase_prompts = {
            TurnoverWheelState.IDLE: "**Strategy: Turnover Wheel** IDLE prompt",
            TurnoverWheelState.SHORT_PUT: "**Strategy: Turnover Wheel** SHORT_PUT prompt",
            TurnoverWheelState.LONG_STOCK: "**Strategy: Turnover Wheel** LONG_STOCK prompt There is NO delta cap on the Turnover Wheel covered call",
            TurnoverWheelState.SHORT_CALL: "**Strategy: Turnover Wheel** SHORT_CALL prompt",
        }
        advisor.spread_prompts = {}
    return advisor


def _make_response(json_payload: dict, stop_reason: str = "end_turn"):
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = [MagicMock()]
    resp.content[0].text = json.dumps(json_payload)
    resp.usage = MagicMock()
    resp.usage.model_dump.return_value = {}
    resp.usage.cache_read_input_tokens = 0
    resp.usage.cache_creation_input_tokens = 0
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50
    return resp


@_skip_no_ca
class TestAskTurnoverWheel:
    """ask_turnover_wheel() sends correct schema and content."""

    def test_uses_turnover_wheel_schema(self):
        """ask_turnover_wheel() calls get_schema('turnover_wheel', phase)."""
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        decision = {
            "action": "sell_put", "symbol": "AAPL250516P00200000",
            "qty": 1, "order_type": "limit", "limit_price": -1.25,
            "reasoning": {"macro": "ok", "fundamental": "ok", "technical": "ok",
                          "volatility": "ok", "selection": "ok", "risk": "ok"},
            "confidence": "high", "skip_reason": None,
        }
        advisor.client.messages.create.return_value = _make_response(decision)

        result = advisor.ask_turnover_wheel({}, TurnoverWheelState.IDLE)

        call_kwargs = advisor.client.messages.create.call_args.kwargs
        fmt = call_kwargs["output_config"]["format"]
        assert fmt["type"] == "json_schema"
        assert fmt["schema"] is get_schema("turnover_wheel", "idle")
        assert result["action"] == "sell_put"

    def test_sends_turnover_wheel_prompt_content(self):
        """ask_turnover_wheel() includes turnover wheel prompt text, not wheel prompt."""
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        decision = {
            "action": "skip", "symbol": None, "qty": 1, "order_type": "limit",
            "limit_price": None,
            "reasoning": {"macro": "no", "fundamental": "—", "technical": "—",
                          "volatility": "—", "selection": "—", "risk": "—"},
            "confidence": "low", "skip_reason": "test",
        }
        advisor.client.messages.create.return_value = _make_response(decision)

        advisor.ask_turnover_wheel({}, TurnoverWheelState.IDLE)

        call_kwargs = advisor.client.messages.create.call_args.kwargs
        # The user message content should contain the turnover wheel prompt text
        user_content = call_kwargs["messages"][0]["content"]
        assert "**Strategy: Turnover Wheel**" in user_content
        # Should NOT contain the standard wheel prompt text
        assert "wheel_prompt_IDLE" not in user_content

    def test_distinctive_phrase_in_long_stock_prompt(self):
        """Distinctive phrase 'NO delta cap' appears in LONG_STOCK prompt."""
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        decision = {
            "action": "sell_call", "symbol": "AAPL250516C00210000",
            "qty": 1, "order_type": "limit", "limit_price": -0.85,
            "reasoning": {"macro": "ok", "fundamental": "ok", "technical": "ok",
                          "volatility": "ok", "selection": "ok", "risk": "ok"},
            "confidence": "high", "skip_reason": None,
        }
        advisor.client.messages.create.return_value = _make_response(decision)

        advisor.ask_turnover_wheel({}, TurnoverWheelState.LONG_STOCK)

        call_kwargs = advisor.client.messages.create.call_args.kwargs
        user_content = call_kwargs["messages"][0]["content"]
        # Distinctive phrase from turnover_wheel_long_stock.md
        assert "NO delta cap on the Turnover Wheel covered call" in user_content

    def test_shares_system_prompt_with_ask(self):
        """ask_turnover_wheel() uses the same system_prompt as ask()."""
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        decision = {
            "action": "skip", "symbol": None, "qty": 1, "order_type": "limit",
            "limit_price": None,
            "reasoning": {"macro": "no", "fundamental": "—", "technical": "—",
                          "volatility": "—", "selection": "—", "risk": "—"},
            "confidence": "low", "skip_reason": "test",
        }
        advisor.client.messages.create.return_value = _make_response(decision)

        advisor.ask_turnover_wheel({}, TurnoverWheelState.IDLE)

        call_kwargs = advisor.client.messages.create.call_args.kwargs
        system_blocks = call_kwargs["system"]
        assert len(system_blocks) == 1
        assert system_blocks[0]["text"] == "SYSTEM PROMPT"

    def test_returns_safe_skip_on_refusal(self):
        """ask_turnover_wheel() returns safe SKIP on stop_reason=refusal."""
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        advisor.client.messages.create.return_value = _make_response(
            {}, stop_reason="refusal"
        )

        result = advisor.ask_turnover_wheel({}, TurnoverWheelState.IDLE)

        assert result["action"] == "skip"
        assert "refusal" in result["skip_reason"]
        assert isinstance(result["reasoning"], dict)

    def test_returns_safe_skip_on_max_tokens(self):
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        advisor.client.messages.create.return_value = _make_response(
            {}, stop_reason="max_tokens"
        )

        result = advisor.ask_turnover_wheel({}, TurnoverWheelState.SHORT_PUT)

        assert result["action"] == "skip"
        assert "max_tokens" in result["skip_reason"]

    def test_raises_on_api_exception(self):
        """Unlike ask_spread(), ask_turnover_wheel() re-raises API errors."""
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        advisor.client.messages.create.side_effect = Exception("Network error")

        with pytest.raises(Exception, match="Network error"):
            advisor.ask_turnover_wheel({}, TurnoverWheelState.IDLE)

    def test_ask_wheel_still_uses_wheel_prompts(self):
        """Regression: ask() must still use wheel phase prompts, not TW prompts."""
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        from strategies.wheel_strategy import WheelState
        decision = {
            "action": "skip", "symbol": None, "qty": 1, "order_type": "limit",
            "limit_price": None,
            "reasoning": {"macro": "no", "fundamental": "—", "technical": "—",
                          "volatility": "—", "selection": "—", "risk": "—"},
            "confidence": "low", "skip_reason": "test",
        }
        advisor.client.messages.create.return_value = _make_response(decision)

        advisor.ask({}, WheelState.IDLE)

        call_kwargs = advisor.client.messages.create.call_args.kwargs
        user_content = call_kwargs["messages"][0]["content"]
        # Standard wheel prompt text must appear
        assert "wheel_prompt_IDLE" in user_content
        # Turnover wheel distinctive phrase must NOT appear
        assert "**Strategy: Turnover Wheel**" not in user_content

    def test_ask_turnover_wheel_logs_strategy_turnover_wheel(self):
        """ask_turnover_wheel() must log strategy=turnover_wheel."""
        advisor = _make_advisor()
        advisor._inject_strategy_params = lambda t, s: t
        decision = {
            "action": "skip", "symbol": None, "qty": 1, "order_type": "limit",
            "limit_price": None,
            "reasoning": {"macro": "no", "fundamental": "—", "technical": "—",
                          "volatility": "—", "selection": "—", "risk": "—"},
            "confidence": "low", "skip_reason": "test",
        }
        advisor.client.messages.create.return_value = _make_response(decision)

        with patch("ai.claude_advisor.logger") as mock_logger:
            advisor.ask_turnover_wheel({}, TurnoverWheelState.IDLE)

        # _record_usage is called with strategy="turnover_wheel"
        # Verify via the structured log line (strategy=turnover_wheel in msg)
        log_calls = [str(c) for c in mock_logger.info.call_args_list]
        turnover_log_found = any("turnover_wheel" in c for c in log_calls)
        assert turnover_log_found, f"Expected 'turnover_wheel' in log calls: {log_calls}"
