# NOT YET ACTIVE — requires account assignment in config
"""Calendar Spread strategy — sell near-term option, buy far-term option, same strike.

Net debit paid. Profits from theta differential (short decays faster)
and positive contango (short-term IV < long-term IV).

State persisted in ``settings.SNAPSHOTS_DIR / "calendar_spread_state.json"``.

# NOTE (Alpaca compatibility, confirmed 2026-04-15):
# Calendar spread mleg orders with different-expiration legs are supported.
# Alpaca's official calendar spread tutorial demonstrates this pattern.
# The execute_entry method below correctly uses place_mleg_order with
# sell_to_open (near-term) and buy_to_open (far-term) legs at different
# expirations encoded in their OCC symbols — this is valid as an mleg order
# because the buy_to_open leg covers the sell_to_open leg.
#
# Rolling the short leg is also possible as a single atomic mleg order:
#   buy_to_close (old short leg) + sell_to_open (new short leg, next expiry)
# This avoids closing the long leg and preserves its time value.
# See: https://alpaca.markets/learn/calendar-spread
#
# TODO: Implement true short-leg rolling as a single atomic mleg order.
# Alpaca supports this: buy_to_close old short + sell_to_open new short
# in one mleg order. This avoids closing the long leg and preserves its
# time value. Currently the ROLL_SHORT action is returned by management
# but the execution path falls back to close-and-reopen. Deferred to
# Phase 4 (calendar rolling enhancement).
"""

import json
import logging
from datetime import datetime
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)


class CalendarSpreadState(str, Enum):
    IDLE = "IDLE"
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"


class CalendarSpreadStrategy:
    """State machine for the calendar spread strategy (INACTIVE — not yet routed)."""

    State = CalendarSpreadState

    def __init__(self, broker, state_writer=None, spread_tracker=None, recorder=None):
        self.broker = broker
        self.state_writer = state_writer
        self.spread_tracker = spread_tracker
        self.recorder = recorder
        self.cb_status_at_entry: str | None = None
        self.state = CalendarSpreadState.IDLE
        self.open_spread_id: str | None = None
        self.pending_order_id: str | None = None
        self._roll_count: int = 0

        self._state_path = settings.SNAPSHOTS_DIR / "calendar_spread_state.json"
        self._load_state()

    # ── state persistence ───────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.state = CalendarSpreadState(data.get("state", "IDLE"))
            self.open_spread_id = data.get("open_spread_id")
            self.pending_order_id = data.get("pending_order_id")
            self._roll_count = data.get("roll_count", 0)
        except Exception:
            logger.exception("Failed to load calendar spread state — starting IDLE")

    def _save_state(self) -> None:
        from utils.fileio import atomic_json_write
        atomic_json_write(self._state_path, {
            "state": self.state.value,
            "open_spread_id": self.open_spread_id,
            "pending_order_id": self.pending_order_id,
            "roll_count": self._roll_count,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def reconcile_pending(self) -> None:
        from strategies._spread_lifecycle import reconcile_pending_state
        reconcile_pending_state(self)

    def get_state(self) -> CalendarSpreadState:
        return self.state

    # ── main cycle ──────────────────────────────────────────

    def run_cycle(self, context: dict, advisor) -> dict:
        if advisor is None:
            raise ValueError("advisor is required")
        if self.state in (
            CalendarSpreadState.PENDING_OPEN, CalendarSpreadState.PENDING_CLOSE,
        ):
            return {
                "action": "HOLD",
                "reasoning": f"Calendar state {self.state.value} — awaiting fill confirmation",
            }
        if self.state == CalendarSpreadState.IDLE:
            return self._evaluate_entry(context, advisor=advisor)
        return self._evaluate_management(context)

    # ── entry evaluation ────────────────────────────────────

    def _evaluate_entry(self, context: dict, advisor=None) -> dict:
        skip = self._check_entry_conditions(context)
        if skip:
            return {"action": "SKIP", "reasoning": skip, "skip_reason": skip}

        cal_data = context.get("spread_candidates", {}).get("calendar_spread", {})
        candidate = cal_data.get("best_candidate")
        if not candidate:
            return {
                "action": "SKIP",
                "reasoning": "No viable calendar spread candidates found",
                "skip_reason": "no_candidates",
            }

        enriched = {**context, "calendar_candidate": candidate}
        return advisor.ask_spread(enriched, "calendar_spread", "idle")

    def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
        """Cheap, Claude-free pre-check. Returns (skip_reason, score)."""
        skip = self._check_entry_conditions(context)
        if skip:
            return skip, 0.0

        cal_data = context.get("spread_candidates", {}).get("calendar_spread", {})
        candidate = cal_data.get("best_candidate")
        if not candidate:
            return "No viable calendar spread candidates found", 0.0

        net_debit = candidate.get("net_debit", 99)
        if net_debit > 2.50 or net_debit <= 0:
            return f"Net debit ${net_debit} outside range $0–$2.50", 0.0

        # Rank by reward potential: lower debit = better for a given spread width
        score = 1.0 / net_debit if net_debit > 0 else 0.0
        return None, score

    def _check_entry_conditions(self, context: dict) -> str | None:
        """Return a skip reason or None if all conditions pass."""
        # Market regime must be NEUTRAL
        regime = context.get("confirmed_market_regime", "NEUTRAL")
        if regime != "NEUTRAL":
            return f"Market regime is {regime}, need NEUTRAL for calendar spread"

        # IV environment must be LOW or MODERATE (not HIGH — long leg is expensive)
        iv_env = context.get("iv_environment", "MODERATE")
        if iv_env == "HIGH":
            return f"IV environment is HIGH — calendar spread is expensive in high IV"

        vol = context.get("volatility") or {}

        # Contango must be NORMAL — the structural edge requires it
        contango_label = vol.get("contango_label")
        if contango_label == "BACKWARDATION":
            return "Term structure in BACKWARDATION — structural edge of calendar spread is gone"
        if contango_label == "FLAT":
            return "Term structure FLAT — contango edge is marginal for calendar spread"

        # IV overvalued label — FAIR or UNDERVALUED preferred (we're buying the long leg)
        iv_ov = vol.get("iv_overvalued_label")
        if iv_ov == "OVERVALUED":
            return "IV is OVERVALUED per ORATS forecast — long leg is expensive for calendar"

        # Earnings check: must not fall between the two expirations
        # This is validated more precisely by guardrails with actual expiry dates.
        # Here we do a conservative check: if earnings <= short_dte_max + 7, skip.
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 35:
            return (
                f"Earnings in {dte_earnings} days — may fall between expirations "
                f"(need > 35 for calendar spread safety)"
            )

        # Technical confirmation — range-bound behavior
        tech = context.get("technicals") or {}
        if tech.get("above_sma_50") is False:
            return "Underlying below 50-day SMA — not range-bound"

        # Already have an active calendar on this underlying
        if self.spread_tracker:
            symbol = context.get("symbol", "")
            existing = self.spread_tracker.get_active_spreads(
                underlying=symbol, strategy_type="calendar_spread",
            )
            if existing:
                return f"Already have an active calendar spread on {symbol}"

        return None

    # ── management evaluation ───────────────────────────────

    def _evaluate_management(self, context: dict) -> dict:
        """Check exit and roll conditions for the open calendar."""
        if not self.open_spread_id or not self.spread_tracker:
            return {"action": "HOLD", "reasoning": "No spread tracker or spread_id"}

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="calendar_spread",
        )
        spread = None
        for s in open_spreads:
            if s["spread_id"] == self.open_spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Open calendar %s not found — resetting to IDLE", self.open_spread_id)
            self.state = CalendarSpreadState.IDLE
            self.open_spread_id = None
            self._roll_count = 0
            self._save_state()
            return {"action": "SKIP", "reasoning": "Calendar not found, reset to IDLE"}

        legs = spread.get("legs", [])
        short_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "sell"]
        long_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "buy"]
        all_symbols = short_symbols + long_symbols

        snapshots = {}
        try:
            snapshots = self.broker.get_option_snapshots(all_symbols, underlying=spread.get("underlying", ""))
        except Exception:
            logger.warning("Failed to fetch snapshots for calendar management")

        missing = [s for s in all_symbols if s not in snapshots or snapshots[s].get("mid") is None]
        if missing:
            return {
                "action": "HOLD",
                "reasoning": f"Cannot evaluate: missing price data for {len(missing)} leg(s)",
            }

        # Current spread value = long_mid - short_mid
        short_value = sum((snapshots.get(s, {}).get("mid") or 0) for s in short_symbols)
        long_value = sum((snapshots.get(s, {}).get("mid") or 0) for s in long_symbols)
        current_value = round(long_value - short_value, 4)

        entry_debit = spread.get("entry_debit", spread.get("entry_credit", 0))
        if not entry_debit or entry_debit <= 0:
            return {"action": "HOLD", "reasoning": "Cannot evaluate: missing entry debit"}

        # P&L as % gain on debit
        pnl_pct = round(((current_value - entry_debit) / entry_debit) * 100, 1)

        # DTE of short leg
        dte_remaining = None
        try:
            exp = datetime.strptime(spread["expiration"], "%Y-%m-%d").date()
            dte_remaining = (exp - datetime.now().date()).days
        except (KeyError, ValueError):
            pass

        # ATR check — if underlying moved > 1 ATR from the strike, close
        underlying_price = (context.get("technicals") or {}).get("current_price")
        atr = (context.get("technicals") or {}).get("atr_14")
        strike = spread.get("strike")
        if underlying_price and atr and strike:
            move_from_strike = abs(underlying_price - strike)
            if move_from_strike > atr:
                return {
                    "action": "CLOSE",
                    "reasoning": (
                        f"Underlying moved {move_from_strike:.2f} > 1 ATR ({atr:.2f}) "
                        f"from strike {strike} — directional thesis invalidated"
                    ),
                    "spread_id": self.open_spread_id,
                }

        # Short leg delta breach — if deep ITM, assignment risk
        for sym in short_symbols:
            snap = snapshots.get(sym, {})
            delta = snap.get("delta")
            if delta is not None and abs(delta) > 0.70:
                return {
                    "action": "CLOSE",
                    "reasoning": (
                        f"Short leg {sym} delta {delta:.2f} > 0.70 — deep ITM, assignment risk"
                    ),
                    "spread_id": self.open_spread_id,
                }

        # Profit target: 50% gain on debit
        if pnl_pct >= 50:
            return {
                "action": "CLOSE",
                "reasoning": f"Captured {pnl_pct}% gain on debit (>= 50% target)",
                "spread_id": self.open_spread_id,
            }

        # Stop loss: 50% loss of debit
        if pnl_pct <= -50:
            return {
                "action": "CLOSE",
                "reasoning": f"Loss of {abs(pnl_pct)}% of debit (>= 50% stop loss)",
                "spread_id": self.open_spread_id,
            }

        # Short leg DTE <= 7: roll or close
        if dte_remaining is not None and dte_remaining <= 7:
            if self._roll_count >= 2:
                return {
                    "action": "CLOSE",
                    "reasoning": f"Short leg DTE {dte_remaining} <= 7 — max 2 rolls reached, closing",
                    "spread_id": self.open_spread_id,
                }
            return {
                "action": "ROLL_SHORT",
                "reasoning": (
                    f"Short leg DTE {dte_remaining} <= 7 — roll to next monthly "
                    f"at same strike for net credit (roll #{self._roll_count + 1})"
                ),
                "spread_id": self.open_spread_id,
            }

        return {
            "action": "HOLD",
            "reasoning": f"Calendar OK. P&L: {pnl_pct}% of debit, DTE: {dte_remaining}",
        }

    # ── execution ───────────────────────────────────────────

    def execute_entry(self, decision: dict) -> bool:
        """Place the calendar spread order (sell short, buy long)."""
        legs = [
            {"symbol": decision["short_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": decision["long_symbol"], "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
        ]

        try:
            order = self.broker.place_mleg_order(
                legs=legs,
                order_type="limit",
                limit_price=decision["limit_price"],
                qty=1,
            )
        except Exception:
            logger.exception("Failed to place calendar spread order")
            return False

        order_id = order.get("id")
        logger.info("Calendar spread order placed: %s", order_id)

        if self.spread_tracker:
            spread_id = self.spread_tracker.register_spread(
                strategy_type="calendar_spread",
                underlying=decision.get("underlying", ""),
                legs=legs,
                entry_credit=decision.get("net_debit", 0),  # stored as debit
                entry_date=datetime.now().date().isoformat(),
                expiration=decision.get("short_expiration", ""),
                max_loss=decision.get("net_debit", 0) * 100,
                max_gain=None,
                entry_order_id=order_id,
                cb_status_at_entry=self.cb_status_at_entry,
            )
            self.open_spread_id = spread_id
            self._roll_count = 0

        self.pending_order_id = order_id
        self.state = CalendarSpreadState.PENDING_OPEN
        self._save_state()
        return True

    def execute_exit(self, spread_id: str, limit_price: float | None = None) -> bool:
        """Close both legs of the calendar spread."""
        if not self.spread_tracker:
            logger.error("No spread tracker — cannot close calendar")
            return False

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="calendar_spread",
        )
        spread = None
        for s in open_spreads:
            if s["spread_id"] == spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Calendar %s not found for exit", spread_id)
            return False

        try:
            close_order = self.broker.close_mleg_position(
                open_legs=spread["legs"],
                order_type="limit" if limit_price else "market",
                limit_price=limit_price,
                qty=1,
            )
        except Exception:
            logger.exception("Failed to close calendar spread %s", spread_id)
            return False

        close_order_id = (close_order or {}).get("id")
        self.spread_tracker.mark_pending_close(spread_id, close_order_id)
        self.pending_order_id = close_order_id
        self.state = CalendarSpreadState.PENDING_CLOSE
        self._save_state()
        logger.info(
            "Calendar spread %s close submitted: %s (PENDING_CLOSE)",
            spread_id, close_order_id,
        )
        return True
