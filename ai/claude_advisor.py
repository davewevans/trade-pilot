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
        self.model = "claude-sonnet-4-5-20251001"
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
        logger.info("Loaded prompt files from %s", _PROMPTS_DIR)

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
        context_json = json.dumps(context, indent=2, default=str)
        user_content = (
            f"{self.phase_prompts[phase]}\n\n"
            f"<market_context>\n{context_json}\n</market_context>\n\n"
            f"Make your decision."
        )

        logger.info("Asking Claude for advice (state=%s)", phase.value)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception:
            logger.exception("Anthropic API call failed")
            raise

        # Store usage for cache monitoring
        self._last_usage = response.usage.model_dump() if response.usage else None

        raw_text = response.content[0].text.strip()
        recommendation = self._parse_response(raw_text)

        logger.info(
            "Claude recommendation: action=%s confidence=%s",
            recommendation["action"],
            recommendation["confidence"],
        )
        logger.info("Reasoning: %s", json.dumps(recommendation["reasoning"], indent=2))

        return recommendation

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
