"""Claude-powered options trading advisor for the wheel strategy."""

import json
import logging
from pathlib import Path

import anthropic

from config import settings
from strategies.wheel_strategy import WheelState

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "prompts"

_REQUIRED_FIELDS = {
    "action", "symbol", "qty", "order_type", "limit_price",
    "reasoning", "confidence", "skip_reason",
}
_VALID_ACTIONS = {"sell_put", "sell_call", "roll", "close", "hold", "skip"}


class ClaudeAdvisor:
    """Uses the Anthropic API to get wheel strategy trade recommendations."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-6"
        self._last_usage: dict | None = None
        self.load_prompts()

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
        ):
            for phase in ("idle", "open"):
                key = f"{strategy}_{phase}"
                path = _PROMPTS_DIR / f"{key}.md"
                if path.exists():
                    self.spread_prompts[key] = path.read_text(encoding="utf-8")
                else:
                    logger.warning("Spread prompt not found: %s", path)
        logger.info("Loaded prompt files from %s", _PROMPTS_DIR)

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
            Dict with keys: action, symbol, qty, order_type, limit_price,
            reasoning, confidence, skip_reason.

        Raises:
            ValueError: If Claude's response is not valid JSON or is missing
                required fields.
        """
        prompt_text = self._inject_strategy_params(self.phase_prompts[phase], "wheel")
        context_json = json.dumps(context, indent=2, default=str)
        user_content = (
            f"<instructions>\n{prompt_text}\n</instructions>\n\n"
            f"<market_context>\n{context_json}\n</market_context>\n\n"
            f"Make your decision now. Respond with raw JSON only."
        )

        logger.info("Asking Claude for advice (state=%s)", phase.value)

        retry_suffix = (
            "\n\nYour previous response was not valid JSON or was missing "
            "required fields. Respond ONLY with raw JSON matching the schema "
            "exactly. No prose, no markdown, no code blocks."
        )

        raw_text = ""
        for attempt in range(2):
            content = user_content if attempt == 0 else user_content + retry_suffix
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=[
                        {
                            "type": "text",
                            "text": self.system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": content}],
                )
            except Exception:
                logger.exception("Anthropic API call failed")
                raise

            self._last_usage = response.usage.model_dump() if response.usage else None
            raw_text = response.content[0].text.strip()

            try:
                recommendation = self._parse_response(raw_text)
                break
            except ValueError:
                if attempt == 0:
                    logger.warning(
                        "Claude response failed to parse on attempt 1; retrying. "
                        "Raw text:\n%s", raw_text,
                    )
                    continue
                logger.warning(
                    "Claude returned unparseable response after retry. "
                    "Falling back to safe skip. Raw text:\n%s", raw_text,
                )
                return {
                    "action": "skip",
                    "symbol": None,
                    "qty": 1,
                    "order_type": "limit",
                    "limit_price": None,
                    "reasoning": {
                        "macro": "Claude response parse error",
                        "fundamental": "—",
                        "technical": "—",
                        "volatility": "—",
                        "selection": "—",
                        "risk": "—",
                    },
                    "confidence": "low",
                    "skip_reason": "Claude returned unparseable response after retry",
                }

        logger.info(
            "Claude recommendation: action=%s confidence=%s",
            recommendation["action"],
            recommendation["confidence"],
        )
        logger.info("Reasoning: %s", json.dumps(recommendation["reasoning"], indent=2))

        return recommendation

    def ask_spread(
        self, context: dict, strategy_type: str, phase: str,
    ) -> dict:
        """Send a spread-strategy decision request to Claude.

        Uses the existing spread schema (uppercase OPEN/SKIP/HOLD/CLOSE
        actions, flat ``reasoning`` string, strategy-specific OCC field
        names). The caller is responsible for enriching ``context``
        with any candidate data the prompt expects.

        Returns the parsed decision dict. On unrecoverable parse
        failure, returns a safe SKIP dict rather than raising.
        """
        key = f"{strategy_type}_{phase}"
        prompt = self.spread_prompts.get(key)
        if prompt is None:
            logger.error("No spread prompt loaded for %s", key)
            return {
                "action": "SKIP",
                "reasoning": f"No spread prompt loaded for {key}",
                "skip_reason": "missing_prompt",
            }

        prompt = self._inject_strategy_params(prompt, strategy_type)
        context_json = json.dumps(context, indent=2, default=str)
        user_content = (
            f"<instructions>\n{prompt}\n</instructions>\n\n"
            f"<market_context>\n{context_json}\n</market_context>\n\n"
            f"Make your decision now. Respond with raw JSON only."
        )

        logger.info(
            "Asking Claude for spread advice (strategy=%s phase=%s)",
            strategy_type, phase,
        )

        retry_suffix = (
            "\n\nYour previous response was not valid JSON or was missing "
            "required fields. Respond ONLY with raw JSON matching the schema "
            "exactly. No prose, no markdown, no code blocks."
        )

        raw_text = ""
        for attempt in range(2):
            content = user_content if attempt == 0 else user_content + retry_suffix
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=[
                        {
                            "type": "text",
                            "text": self.system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": content}],
                )
            except Exception:
                logger.exception("Anthropic API call failed for spread %s", key)
                return {
                    "action": "SKIP",
                    "reasoning": "Claude API error",
                    "skip_reason": "api_error",
                }

            self._last_usage = response.usage.model_dump() if response.usage else None
            raw_text = response.content[0].text.strip()

            try:
                return self._parse_spread_response(raw_text)
            except ValueError:
                if attempt == 0:
                    logger.warning(
                        "Spread response failed to parse on attempt 1; retrying. "
                        "Raw text:\n%s", raw_text,
                    )
                    continue
                logger.warning(
                    "Spread response unparseable after retry. Falling back to "
                    "SKIP. Raw text:\n%s", raw_text,
                )
                return {
                    "action": "SKIP",
                    "reasoning": "Claude returned unparseable response after retry",
                    "skip_reason": "parse_error",
                }

        # Unreachable, but keeps type-checkers happy
        return {
            "action": "SKIP",
            "reasoning": "Unexpected exit from ask_spread loop",
            "skip_reason": "internal_error",
        }

    @staticmethod
    def _parse_spread_response(raw_text: str) -> dict:
        """Parse a spread-strategy JSON response. Raises ValueError on failure."""
        cleaned = raw_text
        if cleaned.startswith("```"):
            first_nl = cleaned.index("\n")
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

        if "action" not in data:
            raise ValueError("Missing required 'action' field")

        return data

    @property
    def prompt_cache_stats(self) -> dict | None:
        """Return token usage from the last API response.

        Includes cache_read_input_tokens and cache_creation_input_tokens
        when prompt caching is active, useful for monitoring savings.
        """
        return self._last_usage

    @staticmethod
    def _parse_response(raw_text: str) -> dict:
        """Parse and validate Claude's JSON response.

        Args:
            raw_text: The raw text from Claude's response.

        Returns:
            Validated recommendation dict.

        Raises:
            ValueError: If the response is not valid JSON or fails validation.
        """
        cleaned = raw_text
        if cleaned.startswith("```"):
            first_newline = cleaned.index("\n")
            cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error("Claude returned invalid JSON. Raw response:\n%s", raw_text)
            raise ValueError(
                f"Claude returned invalid JSON: {e}\nRaw response:\n{raw_text}"
            ) from e

        missing = _REQUIRED_FIELDS - set(data.keys())
        if missing:
            logger.error(
                "Claude response missing required fields: %s\nResponse: %s",
                missing, data,
            )
            raise ValueError(
                f"Claude response missing required fields: {missing}\n"
                f"Response: {data}"
            )

        if data["action"] not in _VALID_ACTIONS:
            raise ValueError(
                f"Invalid action '{data['action']}'. "
                f"Must be one of: {_VALID_ACTIONS}"
            )

        return data
