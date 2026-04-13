"""Long Call Vertical strategy — directional debit spread for bullish markets.

Buy ATM/ITM call + sell higher OTM call, same expiration.
Net debit paid; profits when underlying rises above break-even.

Speculative, directional. Deploy only in LOW IV + BULL regime with a
confirmed support bounce (CAHOLD) signal.

State persisted in ``settings.SNAPSHOTS_DIR / "long_call_vertical_state.json"``.
"""

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path

import anthropic

from config import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "prompts"


class LongCallVerticalState(str, Enum):
    IDLE = "IDLE"
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"


class LongCallVerticalStrategy:
    """State machine for the long call vertical (bull call debit spread)."""

    State = LongCallVerticalState

    def __init__(self, broker, state_writer=None, spread_tracker=None):
        self.broker = broker
        self.state_writer = state_writer
        self.spread_tracker = spread_tracker
        self.state = LongCallVerticalState.IDLE
        self.open_spread_id: str | None = None
        self.pending_order_id: str | None = None
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._model = "claude-sonnet-4-6"

        self._system_prompt = (_PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
        self._idle_prompt = (_PROMPTS_DIR / "long_call_vertical_idle.md").read_text(encoding="utf-8")
        self._open_prompt = (_PROMPTS_DIR / "long_call_vertical_open.md").read_text(encoding="utf-8")

        self._state_path = settings.SNAPSHOTS_DIR / "long_call_vertical_state.json"
        self._load_state()

    # ── state persistence ───────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.state = LongCallVerticalState(data.get("state", "IDLE"))
            self.open_spread_id = data.get("open_spread_id")
            self.pending_order_id = data.get("pending_order_id")
        except Exception:
            logger.exception("Failed to load long call vertical state — starting IDLE")

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps({
            "state": self.state.value,
            "open_spread_id": self.open_spread_id,
            "pending_order_id": self.pending_order_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")

    def reconcile_pending(self) -> None:
        """Resolve PENDING_OPEN / PENDING_CLOSE against broker order status."""
        from strategies._spread_lifecycle import reconcile_pending_state
        reconcile_pending_state(self)

    def get_state(self) -> LongCallVerticalState:
        return self.state

    # ── main cycle ──────────────────────────────────────────

    def run_cycle(self, context: dict, advisor=None) -> dict:
        if self.state in (
            LongCallVerticalState.PENDING_OPEN, LongCallVerticalState.PENDING_CLOSE,
        ):
            return {
                "action": "HOLD",
                "reasoning": f"Spread state {self.state.value} — awaiting fill confirmation",
            }
        if self.state == LongCallVerticalState.IDLE:
            return self._evaluate_entry(context, advisor=advisor)
        return self._evaluate_management(context)

    # ── entry evaluation ────────────────────────────────────

    def _evaluate_entry(self, context: dict, advisor=None) -> dict:
        skip = self._check_entry_conditions(context)
        if skip:
            return {"action": "SKIP", "reasoning": skip, "skip_reason": skip}

        spread_candidates = context.get("spread_candidates", {})
        lcv_data = spread_candidates.get("long_call_vertical", {})
        best = lcv_data.get("best_candidate")
        if not best:
            return {
                "action": "SKIP",
                "reasoning": "No viable long call vertical candidates found",
                "skip_reason": "no_candidates",
            }

        if best.get("net_debit", 0) <= 0 or best.get("net_debit", 99) >= 2.00:
            return {
                "action": "SKIP",
                "reasoning": f"Net debit ${best.get('net_debit', 0)} outside range (need $0.20-$2.00)",
                "skip_reason": "bad_debit",
            }

        if advisor is not None:
            enriched = {**context, "best_candidate": best}
            return advisor.ask_spread(enriched, "long_call_vertical", "idle")
        return self._ask_claude_entry(context, best)

    def _check_entry_conditions(self, context: dict) -> str | None:
        regime = context.get("confirmed_market_regime", "NEUTRAL")
        if regime != "BULL":
            return f"Market regime is {regime}, need BULL"

        iv_env = context.get("iv_environment", "MODERATE")
        if iv_env != "LOW":
            return f"IV environment is {iv_env}, need LOW (don't buy expensive options)"

        ivr = context.get("iv_rank")
        if ivr is not None and ivr >= 30:
            return f"IV rank {ivr} >= 30 (need < 30 for debit spread)"

        tech = context.get("technicals") or {}
        if tech.get("above_sma_50") is not True:
            return "Underlying not above 50-day SMA"

        # Support bounce signal
        bounce = context.get("support_bounce_signal") or {}
        if not bounce.get("cahold_detected"):
            return "No CAHOLD support bounce signal detected"

        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 65:
            # Need earnings > DTE + 5 buffer. Max DTE is 60, so 65 is safe.
            # More precise check happens in guardrails with actual DTE.
            pass

        if self.spread_tracker:
            symbol = context.get("symbol", "")
            existing = self.spread_tracker.get_active_spreads(
                underlying=symbol, strategy_type="long_call_vertical",
            )
            if existing:
                return f"Already have an active long call vertical on {symbol}"

        return None

    def _ask_claude_entry(self, context: dict, best_candidate: dict) -> dict:
        context_json = json.dumps({
            **context,
            "best_candidate": best_candidate,
        }, indent=2, default=str)

        user_content = (
            f"{self._idle_prompt}\n\n"
            f"<market_context>\n{context_json}\n</market_context>\n\n"
            f"Make your decision."
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": self._system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_content}],
            )
            raw = response.content[0].text.strip()
            return self._parse_response(raw)
        except Exception:
            logger.exception("Claude API call failed for long call vertical entry")
            return {"action": "SKIP", "reasoning": "Claude API error", "skip_reason": "api_error"}

    # ── management evaluation ───────────────────────────────

    def _evaluate_management(self, context: dict) -> dict:
        if not self.open_spread_id or not self.spread_tracker:
            return {"action": "HOLD", "reasoning": "No spread tracker or spread_id"}

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="long_call_vertical",
        )
        spread = None
        for s in open_spreads:
            if s["spread_id"] == self.open_spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Open spread %s not found — resetting to IDLE", self.open_spread_id)
            self.state = LongCallVerticalState.IDLE
            self.open_spread_id = None
            self._save_state()
            return {"action": "SKIP", "reasoning": "Spread not found, reset to IDLE"}

        # Current position value: long_mid - short_mid (debit spread value)
        legs = spread.get("legs", [])
        buy_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "buy"]
        sell_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "sell"]

        snapshots = {}
        try:
            snapshots = self.broker.get_option_snapshots(buy_symbols + sell_symbols)
        except Exception:
            logger.warning("Failed to fetch snapshots for long call vertical management")

        buy_value = sum((snapshots.get(s, {}).get("mid") or 0) for s in buy_symbols)
        sell_value = sum((snapshots.get(s, {}).get("mid") or 0) for s in sell_symbols)
        current_value = round(buy_value - sell_value, 4)

        original_debit = spread.get("entry_credit", 0)  # stored as positive
        max_gain = spread.get("max_gain", 0) / 100 if spread.get("max_gain") else 0

        # gain_pct = how much of max gain captured
        if max_gain > 0:
            gain_pct = round(((current_value - original_debit) / max_gain) * 100, 1)
        else:
            gain_pct = 0

        # DTE
        dte_remaining = None
        try:
            exp = datetime.strptime(spread["expiration"], "%Y-%m-%d").date()
            dte_remaining = (exp - datetime.now().date()).days
        except (KeyError, ValueError):
            pass

        # Exit conditions

        # 1. Profit target: 75% of max gain
        if gain_pct >= 75:
            return {
                "action": "CLOSE",
                "reasoning": f"Captured {gain_pct}% of max gain (>= 75% target)",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        # 2. Time exit: DTE <= 20
        if dte_remaining is not None and dte_remaining <= 20:
            return {
                "action": "CLOSE",
                "reasoning": f"DTE {dte_remaining} <= 20 (theta acceleration)",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        # 3. Stop loss: spread value fallen 40% from debit
        if original_debit > 0 and current_value < original_debit * 0.60:
            return {
                "action": "CLOSE",
                "reasoning": (
                    f"Spread value ${current_value:.2f} fallen below 60% of "
                    f"original debit ${original_debit:.2f} (stop loss)"
                ),
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        return {
            "action": "HOLD",
            "reasoning": f"Position OK. Gain: {gain_pct}%, DTE: {dte_remaining}",
        }

    # ── execution ───────────────────────────────────────────

    def execute_entry(self, decision: dict) -> bool:
        legs = [
            {"symbol": decision["long_call_symbol"], "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
            {"symbol": decision["short_call_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
        ]

        try:
            order = self.broker.place_mleg_order(
                legs=legs,
                order_type="limit",
                limit_price=decision["limit_price"],  # positive for debit
                qty=1,
            )
        except Exception:
            logger.exception("Failed to place long call vertical order")
            return False

        order_id = order.get("id")
        logger.info("Long call vertical order placed: %s", order_id)

        if self.spread_tracker:
            wing_width = decision.get("short_call_strike", 0) - decision.get("long_call_strike", 0)
            spread_id = self.spread_tracker.register_spread(
                strategy_type="long_call_vertical",
                underlying=decision.get("underlying", ""),
                legs=legs,
                entry_credit=decision.get("net_debit", 0),  # stored as positive
                entry_date=datetime.now().date().isoformat(),
                expiration=decision.get("expiration", ""),
                max_loss=round(decision.get("net_debit", 0) * 100, 2),
                max_gain=round((wing_width - decision.get("net_debit", 0)) * 100, 2),
                entry_order_id=order_id,
            )
            self.open_spread_id = spread_id

        self.pending_order_id = order_id
        self.state = LongCallVerticalState.PENDING_OPEN
        self._save_state()

        if self.state_writer:
            try:
                self.state_writer.write_decision(
                    decision_dict=decision,
                    reasoning=decision.get("reasoning", ""),
                    action_taken=True,
                    underlying=decision.get("underlying", ""),
                )
            except Exception:
                logger.warning("Failed to write long call vertical decision snapshot")

        return True

    def execute_exit(self, spread_id: str, limit_price: float | None = None) -> bool:
        if not self.spread_tracker:
            logger.error("No spread tracker — cannot close")
            return False

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="long_call_vertical",
        )
        spread = None
        for s in open_spreads:
            if s["spread_id"] == spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Spread %s not found for exit", spread_id)
            return False

        try:
            close_order = self.broker.close_mleg_position(
                open_legs=spread["legs"],
                order_type="limit" if limit_price else "market",
                limit_price=limit_price,
                qty=1,
            )
        except Exception:
            logger.exception("Failed to close long call vertical %s", spread_id)
            return False

        close_order_id = (close_order or {}).get("id")
        self.spread_tracker.mark_pending_close(spread_id, close_order_id)
        self.pending_order_id = close_order_id
        self.state = LongCallVerticalState.PENDING_CLOSE
        self._save_state()
        logger.info(
            "Long call vertical %s close submitted: %s (PENDING_CLOSE)",
            spread_id, close_order_id,
        )
        return True

    # ── parsing ─────────────────────────────────────────────

    @staticmethod
    def _parse_response(raw_text: str) -> dict:
        cleaned = raw_text
        if cleaned.startswith("```"):
            first_nl = cleaned.index("\n")
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("Claude returned invalid JSON for long call vertical:\n%s", raw_text)
            return {"action": "SKIP", "reasoning": "Invalid JSON response", "skip_reason": "parse_error"}

        if "action" not in data:
            return {"action": "SKIP", "reasoning": "Missing action field", "skip_reason": "parse_error"}

        return data
