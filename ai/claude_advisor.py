"""Claude-powered options trading advisor.

Uses Anthropic structured outputs (output_config.format.json_schema) so
every response is guaranteed schema-conformant. No parse-retry loop and
no markdown-fence stripping are needed.

NOTE: adding output_config.format modifies Anthropic's auto-injected
system prompt addendum, which invalidates the prompt cache on the FIRST
call after deploy. The cache rebuilds on the second call and remains
warm as long as the schema is unchanged.

Story 2B: structured outputs active.
"""

import json
import logging
import time
from pathlib import Path

import anthropic

import config as _config
from ai.schemas import get_schema
from config import settings
from strategies.turnover_wheel_strategy import TurnoverWheelState
from strategies.wheel_strategy import WheelState

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "prompts"


def _cache_hit_pct(cache_read: int, cache_write: int, input_tokens: int) -> float:
    """Return cache_read / total_tokens as a percentage, rounded to 1 decimal."""
    total = cache_read + cache_write + input_tokens
    if total == 0:
        return 0.0
    return round(cache_read / total * 100, 1)


def _safe_skip_wheel(reason: str, skip_code: str | None = None) -> dict:
    """Return a safe SKIP dict matching the wheel schema shape."""
    result: dict = {
        "action": "skip",
        "symbol": None,
        "qty": 1,
        "order_type": "limit",
        "limit_price": None,
        "reasoning": {
            "macro": reason,
            "fundamental": "—",
            "technical": "—",
            "volatility": "—",
            "selection": "—",
            "risk": "—",
        },
        "confidence": "low",
        "skip_reason": reason,
    }
    if skip_code is not None:
        result["skip_reason_code"] = skip_code
    return result


def _safe_skip_spread(strategy: str, phase: str, reason: str, skip_code: str | None = None) -> dict:
    """Return a safe SKIP dict matching the spread schema shape."""
    result: dict = {
        "action": "SKIP",
        "reasoning": {
            "macro": reason,
            "fundamental": "—",
            "technical": "—",
            "volatility": "—",
            "selection": "—",
            "risk": "—",
        },
        "confidence": "low",
        "skip_reason": reason,
    }
    if skip_code is not None:
        result["skip_reason_code"] = skip_code
    return result


def _log_schema_failure(
    strategy: str,
    phase: str,
    raw_text: str | None,
    stop_reason: str | None,
    exception: Exception,
) -> None:
    """Append a failed-schema response to data/logs/schema_failures.jsonl.

    Never raises — logging is best-effort diagnostic support. The file is
    ops-level (not DB-tracked) and acts as the corpus for diagnosing what
    Claude gets wrong when structured outputs fail.
    """
    try:
        import datetime as _dt
        log_path = settings.LOG_DIR / "schema_failures.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "timestamp": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "strategy": strategy,
            "phase": phase,
            "stop_reason": stop_reason,
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "raw_text": (raw_text or "")[:8000],
        })
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(entry + "\n")
    except Exception as write_exc:
        logger.warning("_log_schema_failure: could not write to schema_failures.jsonl: %s", write_exc)


_VALID_THINKING_MODES = {"off", "adaptive_medium", "adaptive_high"}


class ClaudeAdvisor:
    """Uses the Anthropic API to get options trading recommendations.

    Structured outputs are always active — schemas are selected per
    (strategy, phase) from ai.schemas and passed as output_config.format.

    Adaptive thinking is controlled by ``thinking_mode`` (default "off").
    Set THINKING_MODE env var or pass the constructor arg directly.
    Do NOT enable in production until the A/B harness (Story 3) validates
    decision improvement — thinking tokens are billed at output rates.
    """

    def __init__(self, api_usage_repo=None, thinking_mode: str | None = None):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.ADVISOR_MODEL
        self._last_usage: dict | None = None
        self._api_usage_repo = api_usage_repo

        mode = thinking_mode if thinking_mode is not None else settings.THINKING_MODE
        if mode not in _VALID_THINKING_MODES:
            logger.warning(
                "Unknown THINKING_MODE=%r — defaulting to 'off'. Valid: %s",
                mode, _VALID_THINKING_MODES,
            )
            mode = "off"
        self.thinking_mode = mode
        if mode != "off":
            logger.info("Adaptive thinking enabled: mode=%s", mode)

        self.prompt_version = "unknown"
        self.load_prompts()

    def _build_output_config(self, schema: dict) -> dict:
        """Build the output_config dict, merging format + effort as needed."""
        cfg: dict = {"format": {"type": "json_schema", "schema": schema}}
        if self.thinking_mode == "adaptive_medium":
            cfg["effort"] = "medium"
        elif self.thinking_mode == "adaptive_high":
            cfg["effort"] = "high"
        return cfg

    def _build_thinking_param(self) -> dict | None:
        """Return the ``thinking`` parameter dict, or None when mode is off."""
        if self.thinking_mode == "off":
            return None
        return {"type": "adaptive", "display": "omitted"}

    def _build_usage_dict(self, usage) -> dict | None:
        """Build the structured usage dict stored in self._last_usage.

        Merges the raw Anthropic UsageBlock fields with our own typed keys and
        a cost estimate frozen at the current pricing.  Returns None when
        ``usage`` is None.

        If the current model version is not in CLAUDE_PRICING the cost is
        recorded as None and a warning is logged — the advisor never crashes.
        """
        if usage is None:
            return None

        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        input_toks = getattr(usage, "input_tokens", 0) or 0
        output_toks = getattr(usage, "output_tokens", 0) or 0

        try:
            pricing = _config.get_pricing(self.model)
            cost: float | None = round(
                (input_toks / 1_000_000) * pricing["input_per_mtok"]
                + (output_toks / 1_000_000) * pricing["output_per_mtok"]
                + (cache_read / 1_000_000) * pricing["cache_read_per_mtok"]
                + (cache_creation / 1_000_000) * pricing["cache_write_per_mtok"],
                6,
            )
        except KeyError:
            logger.warning(
                "Unknown model version %r — cannot estimate cost; storing NULL",
                self.model,
            )
            cost = None

        return {
            # Raw Anthropic fields (kept for backward-compat with any reader
            # that already uses prompt_cache_stats).
            **usage.model_dump(),
            # Structured fields consumed by DecisionRepository.
            "model_version": self.model,
            "input_tokens": input_toks,
            "output_tokens": output_toks,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "estimated_cost_usd": cost,
        }

    def _record_usage(
        self,
        usage,
        *,
        strategy: str | None,
        phase: str | None,
        latency_ms: int,
    ) -> None:
        """Emit a structured log line and persist usage stats to DB.

        ``usage`` is the Anthropic UsageBlock (or None). Never raises.
        """
        if usage is None:
            cache_read = cache_write = input_tokens = output_tokens = 0
        else:
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0

        hit_pct = _cache_hit_pct(cache_read, cache_write, input_tokens)
        logger.info(
            "claude_api_usage strategy=%s phase=%s input=%d cache_read=%d "
            "cache_write=%d output=%d cache_hit_pct=%.1f structured_outputs=true thinking_mode=%s",
            strategy, phase, input_tokens, cache_read, cache_write, output_tokens, hit_pct,
            self.thinking_mode,
        )

        if self._api_usage_repo is not None:
            try:
                self._api_usage_repo.record_api_call(
                    strategy=strategy,
                    phase=phase,
                    model=self.model,
                    input_tokens=input_tokens,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                )
            except Exception:
                logger.warning("Failed to persist API usage to DB", exc_info=True)

    def load_prompts(self) -> None:
        """Load (or reload) all prompt files from disk.

        Call this to hot-reload prompts without restarting the bot.
        """
        self.system_prompt = (_PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
        self.phase_prompts = {
            WheelState.IDLE: (_PROMPTS_DIR / "wheel_idle.md").read_text(encoding="utf-8"),
            WheelState.SHORT_PUT: (_PROMPTS_DIR / "wheel_short_put.md").read_text(encoding="utf-8"),
            WheelState.LONG_STOCK: (_PROMPTS_DIR / "wheel_long_stock.md").read_text(encoding="utf-8"),
            WheelState.SHORT_CALL: (_PROMPTS_DIR / "wheel_short_call.md").read_text(encoding="utf-8"),
        }
        self.spread_prompts: dict[str, str] = {}
        for strategy in (
            "bull_put_spread", "bear_call_spread",
            "iron_condor", "long_call_vertical",
            "iron_butterfly", "calendar_spread",
        ):
            for phase in ("idle", "open"):
                key = f"{strategy}_{phase}"
                path = _PROMPTS_DIR / f"{key}.md"
                if path.exists():
                    self.spread_prompts[key] = path.read_text(encoding="utf-8")
                else:
                    logger.warning("Spread prompt not found: %s", path)

        self.turnover_wheel_phase_prompts: dict[TurnoverWheelState, str] = {
            TurnoverWheelState.IDLE: (_PROMPTS_DIR / "turnover_wheel_idle.md").read_text(encoding="utf-8"),
            TurnoverWheelState.SHORT_PUT: (_PROMPTS_DIR / "turnover_wheel_short_put.md").read_text(encoding="utf-8"),
            TurnoverWheelState.LONG_STOCK: (_PROMPTS_DIR / "turnover_wheel_long_stock.md").read_text(encoding="utf-8"),
            TurnoverWheelState.SHORT_CALL: (_PROMPTS_DIR / "turnover_wheel_short_call.md").read_text(encoding="utf-8"),
        }

        logger.info("Loaded prompt files from %s", _PROMPTS_DIR)
        self.prompt_version = self._compute_prompt_version()
        logger.info("Prompt version: %s", self.prompt_version)

    def _compute_prompt_version(self) -> str:
        """SHA256-12 of concatenated prompt file contents.

        Walks _PROMPTS_DIR, reads every .md file in sorted order, joins them
        with a file boundary marker, and hashes. Any read error → "unknown".
        Returns first 12 hex chars of the digest.
        """
        import hashlib
        try:
            if not _PROMPTS_DIR.exists():
                return "unknown"
            md_files = sorted(_PROMPTS_DIR.glob("*.md"))
            parts: list[str] = []
            for path in md_files:
                parts.append(f"---FILE:{path.name}---")
                parts.append(path.read_text(encoding="utf-8"))
            joined = "\n".join(parts)
            digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
            return digest[:12]
        except Exception:
            logger.warning("Failed to compute prompt version", exc_info=True)
            return "unknown"

    def _inject_strategy_params(self, prompt_text: str, strategy_name: str) -> str:
        """Replace {{param_name}} placeholders with values from strategy definition.

        Flattens entry + management + guardrails into a single lookup dict.
        Nested dicts (e.g. wheel entry.csp) are prefixed: csp_delta_min.
        Unknown {{keys}} are left as-is with a debug log.
        """
        from strategies.strategy_loader import load_strategy
        import re

        try:
            defn = load_strategy(strategy_name)
        except FileNotFoundError:
            logger.warning(
                "No strategy definition for %s — using raw prompt", strategy_name,
            )
            return prompt_text

        replacements: dict[str, str] = {}
        for section in ("entry", "management", "guardrails"):
            section_data = defn.get(section, {})
            if not isinstance(section_data, dict):
                continue
            for k, v in section_data.items():
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        replacements[f"{k}_{k2}"] = str(v2)
                else:
                    replacements[k] = str(v)

        def replace_match(match: re.Match) -> str:
            key = match.group(1)
            if key in replacements:
                return replacements[key]
            logger.debug(
                "Prompt template key {{%s}} not found in strategy %s — leaving as-is",
                key, strategy_name,
            )
            return match.group(0)

        return re.sub(r'\{\{(\w+)\}\}', replace_match, prompt_text)

    def ask(self, context: dict, phase: WheelState) -> dict:
        """Send the current market context to Claude and get a trade recommendation.

        Args:
            context: The full context dict from ContextBuilder.build().
            phase: The current WheelState.

        Returns:
            Dict matching the wheel schema for this phase. On stop_reason
            refusal or max_tokens, returns a safe SKIP dict instead of raising.

        Raises:
            Exception: On Anthropic API network/auth failures (re-raised so
                the caller can log and decide how to handle).
        """
        phase_str = phase.value if hasattr(phase, "value") else str(phase)
        prompt_text = self._inject_strategy_params(self.phase_prompts[phase], "wheel")
        context_json = json.dumps(context, indent=2, default=str)
        user_content = (
            f"<instructions>\n{prompt_text}\n</instructions>\n\n"
            f"<market_context>\n{context_json}\n</market_context>\n\n"
            f"Make your trading decision now."
        )

        logger.info("Asking Claude for advice (state=%s)", phase_str)

        schema = get_schema("wheel", phase_str.lower())
        output_config = self._build_output_config(schema)
        thinking = self._build_thinking_param()

        try:
            _t0 = time.monotonic()
            create_kwargs: dict = dict(
                model=self.model,
                max_tokens=16000 if thinking else 2048,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
                output_config=output_config,
            )
            if thinking is not None:
                create_kwargs["thinking"] = thinking
            response = self.client.messages.create(**create_kwargs)
            _latency_ms = int((time.monotonic() - _t0) * 1000)
        except Exception:
            logger.exception("Anthropic API call failed")
            raise

        self._last_usage = self._build_usage_dict(response.usage)
        self._record_usage(
            response.usage,
            strategy="wheel",
            phase=phase_str,
            latency_ms=_latency_ms,
        )

        if response.stop_reason in ("refusal", "max_tokens"):
            logger.error(
                "Claude stop_reason=%s for wheel/%s — falling back to safe SKIP",
                response.stop_reason, phase_str,
            )
            # TODO: plumb distinct codes for refusal/max_tokens (SkipReason.CLAUDE_REFUSAL / CLAUDE_MAX_TOKENS)
            return _safe_skip_wheel(f"Claude stop_reason: {response.stop_reason}")

        try:
            recommendation = json.loads(response.content[0].text)
            logger.info(
                "Claude recommendation: action=%s confidence=%s",
                recommendation["action"],
                recommendation.get("confidence"),
            )
            logger.info("Reasoning: %s", json.dumps(recommendation.get("reasoning"), indent=2))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raw = getattr(response.content[0], "text", None) if response.content else None
            logger.error(
                "wheel/%s schema parse failure (%s: %s) — raw response (first 200 chars): %s",
                phase_str, type(exc).__name__, exc, (raw or "")[:200],
            )
            from strategies.skip_codes import SkipCode
            _log_schema_failure("wheel", phase_str, raw, response.stop_reason, exc)
            return _safe_skip_wheel("Schema validation failed: SCHEMA_INVALID", skip_code=SkipCode.SCHEMA_INVALID)

        return recommendation

    def ask_turnover_wheel(self, context: dict, phase: TurnoverWheelState) -> dict:
        """Send the current market context to Claude and get a Turnover Wheel recommendation.

        Mirrors ask() but uses the turnover_wheel prompt files, the shared
        system prompt (not a separate one), and selects the turnover_wheel schema
        from the registry.

        Args:
            context: The full context dict from ContextBuilder.build().
            phase: The current TurnoverWheelState.

        Returns:
            Dict matching the wheel schema for this phase. On stop_reason
            refusal or max_tokens, returns a safe SKIP dict instead of raising.

        Raises:
            Exception: On Anthropic API network/auth failures (re-raised so
                the caller can log and decide how to handle).
        """
        phase_str = phase.value if hasattr(phase, "value") else str(phase)
        prompt_text = self._inject_strategy_params(
            self.turnover_wheel_phase_prompts[phase], "turnover_wheel"
        )
        context_json = json.dumps(context, indent=2, default=str)
        user_content = (
            f"<instructions>\n{prompt_text}\n</instructions>\n\n"
            f"<market_context>\n{context_json}\n</market_context>\n\n"
            f"Make your trading decision now."
        )

        logger.info("Asking Claude for turnover wheel advice (state=%s)", phase_str)

        schema = get_schema("turnover_wheel", phase_str.lower())
        output_config = self._build_output_config(schema)
        thinking = self._build_thinking_param()

        try:
            _t0 = time.monotonic()
            create_kwargs: dict = dict(
                model=self.model,
                max_tokens=16000 if thinking else 2048,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
                output_config=output_config,
            )
            if thinking is not None:
                create_kwargs["thinking"] = thinking
            response = self.client.messages.create(**create_kwargs)
            _latency_ms = int((time.monotonic() - _t0) * 1000)
        except Exception:
            logger.exception("Anthropic API call failed")
            raise

        self._last_usage = self._build_usage_dict(response.usage)
        self._record_usage(
            response.usage,
            strategy="turnover_wheel",
            phase=phase_str,
            latency_ms=_latency_ms,
        )

        if response.stop_reason in ("refusal", "max_tokens"):
            logger.error(
                "Claude stop_reason=%s for turnover_wheel/%s — falling back to safe SKIP",
                response.stop_reason, phase_str,
            )
            # TODO: plumb distinct codes for refusal/max_tokens (SkipReason.CLAUDE_REFUSAL / CLAUDE_MAX_TOKENS)
            return _safe_skip_wheel(f"Claude stop_reason: {response.stop_reason}")

        try:
            recommendation = json.loads(response.content[0].text)
            logger.info(
                "Claude turnover wheel recommendation: action=%s confidence=%s",
                recommendation["action"],
                recommendation.get("confidence"),
            )
            logger.info("Reasoning: %s", json.dumps(recommendation.get("reasoning"), indent=2))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raw = getattr(response.content[0], "text", None) if response.content else None
            logger.error(
                "turnover_wheel/%s schema parse failure (%s: %s) — raw response (first 200 chars): %s",
                phase_str, type(exc).__name__, exc, (raw or "")[:200],
            )
            from strategies.skip_codes import SkipCode
            _log_schema_failure("turnover_wheel", phase_str, raw, response.stop_reason, exc)
            return _safe_skip_wheel("Schema validation failed: SCHEMA_INVALID", skip_code=SkipCode.SCHEMA_INVALID)

        return recommendation

    def ask_spread(
        self, context: dict, strategy_type: str, phase: str,
    ) -> dict:
        """Send a spread-strategy decision request to Claude.

        Uses structured outputs — the schema for (strategy_type, phase) is
        selected from ai.schemas and passed to output_config. The response
        is guaranteed schema-conformant; no retry loop or parsing code needed.

        Returns the decision dict. On stop_reason refusal/max_tokens or
        API exception, returns a safe SKIP dict rather than raising.
        """
        key = f"{strategy_type}_{phase}"
        prompt = self.spread_prompts.get(key)
        if prompt is None:
            logger.error("No spread prompt loaded for %s", key)
            return _safe_skip_spread(strategy_type, phase, f"No spread prompt loaded for {key}")

        try:
            schema = get_schema(strategy_type, phase)
        except KeyError:
            logger.error("No structured output schema for %s/%s", strategy_type, phase)
            return _safe_skip_spread(strategy_type, phase, f"No schema for {key}")

        output_config = self._build_output_config(schema)
        thinking = self._build_thinking_param()

        prompt = self._inject_strategy_params(prompt, strategy_type)
        context_json = json.dumps(context, indent=2, default=str)
        user_content = (
            f"<instructions>\n{prompt}\n</instructions>\n\n"
            f"<market_context>\n{context_json}\n</market_context>\n\n"
            f"Make your trading decision now."
        )

        logger.info(
            "Asking Claude for spread advice (strategy=%s phase=%s)",
            strategy_type, phase,
        )

        try:
            _t0 = time.monotonic()
            create_kwargs: dict = dict(
                model=self.model,
                max_tokens=16000 if thinking else 2048,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
                output_config=output_config,
            )
            if thinking is not None:
                create_kwargs["thinking"] = thinking
            response = self.client.messages.create(**create_kwargs)
            _latency_ms = int((time.monotonic() - _t0) * 1000)
        except Exception:
            logger.exception("Anthropic API call failed for spread %s", key)
            return _safe_skip_spread(strategy_type, phase, "Claude API error")

        self._last_usage = self._build_usage_dict(response.usage)
        self._record_usage(
            response.usage,
            strategy=strategy_type,
            phase=phase,
            latency_ms=_latency_ms,
        )

        if response.stop_reason in ("refusal", "max_tokens"):
            logger.error(
                "Claude stop_reason=%s for %s/%s — falling back to safe SKIP",
                response.stop_reason, strategy_type, phase,
            )
            # TODO: plumb distinct codes for refusal/max_tokens (SkipReason.CLAUDE_REFUSAL / CLAUDE_MAX_TOKENS)
            return _safe_skip_spread(strategy_type, phase, f"Claude stop_reason: {response.stop_reason}")

        try:
            return json.loads(response.content[0].text)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raw = getattr(response.content[0], "text", None) if response.content else None
            logger.error(
                "%s/%s schema parse failure (%s: %s) — raw response (first 200 chars): %s",
                strategy_type, phase, type(exc).__name__, exc, (raw or "")[:200],
            )
            from strategies.skip_codes import SkipCode
            _log_schema_failure(strategy_type, phase, raw, response.stop_reason, exc)
            return _safe_skip_spread(strategy_type, phase, "Schema validation failed: SCHEMA_INVALID", skip_code=SkipCode.SCHEMA_INVALID)

    @property
    def prompt_cache_stats(self) -> dict | None:
        """Return token usage from the last API response.

        Includes cache_read_input_tokens and cache_creation_input_tokens
        when prompt caching is active, useful for monitoring savings.
        """
        return self._last_usage

    def count_system_prompt_tokens(self) -> int | None:
        """Use the token counting API to measure the system prompt size.

        Free endpoint, no cost. Returns token count or None on failure.
        """
        try:
            response = self.client.messages.count_tokens(
                model=self.model,
                system=[{"type": "text", "text": self.system_prompt}],
                messages=[{"role": "user", "content": "test"}],
            )
            return response.input_tokens
        except Exception:
            logger.warning("Token counting failed", exc_info=True)
            return None
