"""Bear Call Spread strategy — defined-risk bearish credit spread.

Sell OTM call + buy higher OTM call, same expiration.
Collects net credit; profits when underlying stays below short call.

One spread at a time per underlying.
State persisted in ``settings.SNAPSHOTS_DIR / "bear_call_spread_state.json"``.
"""

import json
import logging
from datetime import datetime
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)


class BearCallSpreadState(str, Enum):
    IDLE = "IDLE"
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"


class BearCallSpreadStrategy:
    """State machine for the bear call spread strategy."""

    State = BearCallSpreadState

    def __init__(self, broker, state_writer=None, spread_tracker=None, recorder=None):
        self.broker = broker
        self.state_writer = state_writer
        self.spread_tracker = spread_tracker
        self.recorder = recorder
        self.cb_status_at_entry: str | None = None
        self.state = BearCallSpreadState.IDLE
        self.open_spread_id: str | None = None
        self.pending_order_id: str | None = None

        self._state_path = settings.SNAPSHOTS_DIR / "bear_call_spread_state.json"
        self._load_state()

    # ── state persistence ───────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.state = BearCallSpreadState(data.get("state", "IDLE"))
            self.open_spread_id = data.get("open_spread_id")
            self.pending_order_id = data.get("pending_order_id")
        except Exception:
            logger.exception("Failed to load bear call spread state — starting IDLE")

    def _save_state(self) -> None:
        from utils.fileio import atomic_json_write
        atomic_json_write(self._state_path, {
            "state": self.state.value,
            "open_spread_id": self.open_spread_id,
            "pending_order_id": self.pending_order_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def reconcile_pending(self) -> None:
        """Resolve PENDING_OPEN / PENDING_CLOSE against broker order status."""
        from strategies._spread_lifecycle import reconcile_pending_state
        reconcile_pending_state(self)

    def get_state(self) -> BearCallSpreadState:
        return self.state

    # ── main cycle ──────────────────────────────────────────

    def run_cycle(self, context: dict, advisor) -> dict:
        if advisor is None:
            raise ValueError("advisor is required")
        if self.state in (
            BearCallSpreadState.PENDING_OPEN, BearCallSpreadState.PENDING_CLOSE,
        ):
            return {
                "action": "HOLD",
                "reasoning": f"Spread state {self.state.value} — awaiting fill confirmation",
            }
        if self.state == BearCallSpreadState.IDLE:
            return self._evaluate_entry(context, advisor=advisor)
        return self._evaluate_management(context)

    # ── entry evaluation ────────────────────────────────────

    def _evaluate_entry(self, context: dict, advisor=None) -> dict:
        skip = self._check_entry_conditions(context)
        if skip:
            return {"action": "SKIP", "reasoning": skip, "skip_reason": skip}

        spread_candidates = context.get("spread_candidates", {})
        bcs_data = spread_candidates.get("bear_call_spread", {})
        best = bcs_data.get("best_candidate")
        if not best:
            return {
                "action": "SKIP",
                "reasoning": "No viable bear call spread candidates found",
                "skip_reason": "no_candidates",
            }

        if best.get("net_credit", 0) <= 0.50:
            return {
                "action": "SKIP",
                "reasoning": f"Best candidate credit ${best.get('net_credit', 0)} <= $0.50",
                "skip_reason": "low_credit",
            }
        if best.get("credit_to_width_ratio", 0) < 0.15:
            return {
                "action": "SKIP",
                "reasoning": f"Credit/width ratio {best.get('credit_to_width_ratio', 0)} < 0.15",
                "skip_reason": "low_ratio",
            }

        enriched = {**context, "best_candidate": best}
        return advisor.ask_spread(enriched, "bear_call_spread", "idle")

    def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
        """Cheap, Claude-free pre-check used to rank candidates across symbols.

        Returns ``(skip_reason, score)``. Score is the candidate net credit.
        """
        skip = self._check_entry_conditions(context)
        if skip:
            return skip, 0.0

        spread_candidates = context.get("spread_candidates", {})
        bcs_data = spread_candidates.get("bear_call_spread", {})
        best = bcs_data.get("best_candidate")
        if not best:
            return "No viable bear call spread candidates found", 0.0

        net_credit = best.get("net_credit", 0)
        if net_credit <= 0.50:
            return f"Best candidate credit ${net_credit} <= $0.50", 0.0
        if best.get("credit_to_width_ratio", 0) < 0.15:
            return (
                f"Credit/width ratio {best.get('credit_to_width_ratio', 0)} < 0.15",
                0.0,
            )

        return None, float(net_credit)

    def _check_entry_conditions(self, context: dict) -> str | None:
        regime = context.get("confirmed_market_regime", "NEUTRAL")
        if regime not in ("BEAR", "NEUTRAL"):
            return f"Market regime is {regime}, need BEAR or NEUTRAL"

        ivr = context.get("iv_rank")
        if ivr is not None and ivr < 40:
            return f"IV rank {ivr} < 40 minimum"

        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 25:
            return f"Earnings in {dte_earnings} days (need > 25)"

        # Ex-dividend within the typical DTE window (40 days)
        days_ex = fund.get("days_to_ex_dividend")
        if days_ex is not None and days_ex <= 40:
            return f"Ex-dividend in {days_ex} days — early assignment risk"

        # Bearish technical justification
        tech = context.get("technicals") or {}
        above_sma_50 = tech.get("above_sma_50")
        above_sma_200 = tech.get("above_sma_200")
        rsi = tech.get("rsi_14")

        has_bearish_setup = (
            above_sma_50 is False
            or (rsi is not None and rsi >= 60)
        )
        if not has_bearish_setup and regime != "BEAR":
            return "No bearish technical setup (above 50-SMA and RSI < 60)"

        # Already have an active spread (includes PENDING states).
        if self.spread_tracker:
            symbol = context.get("symbol", "")
            open_bcs = self.spread_tracker.get_active_spreads(
                underlying=symbol, strategy_type="bear_call_spread",
            )
            if open_bcs:
                return f"Already have an active bear call spread on {symbol}"

        return None

    # ── management evaluation ───────────────────────────────

    def _evaluate_management(self, context: dict) -> dict:
        if not self.open_spread_id or not self.spread_tracker:
            return {"action": "HOLD", "reasoning": "No spread tracker or spread_id"}

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="bear_call_spread",
        )
        spread = None
        for s in open_spreads:
            if s["spread_id"] == self.open_spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Open spread %s not found — resetting to IDLE", self.open_spread_id)
            self.state = BearCallSpreadState.IDLE
            self.open_spread_id = None
            self._save_state()
            return {"action": "SKIP", "reasoning": "Spread not found, reset to IDLE"}

        # Current position value
        legs = spread.get("legs", [])
        short_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "sell"]
        long_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "buy"]

        all_symbols = short_symbols + long_symbols
        snapshots = {}
        try:
            snapshots = self.broker.get_option_snapshots(all_symbols)
        except Exception:
            logger.warning("Failed to fetch snapshots for bear call spread management")

        missing = [s for s in all_symbols if s not in snapshots or snapshots[s].get("mid") is None]
        if missing:
            logger.warning(
                "%s management: missing snapshots for %s — defaulting to HOLD",
                type(self).__name__, missing,
            )
            return {
                "action": "HOLD",
                "reasoning": f"Cannot evaluate: missing price data for {len(missing)} leg(s)",
            }

        short_value = sum(
            (snapshots.get(s, {}).get("mid") or 0) for s in short_symbols
        )
        long_value = sum(
            (snapshots.get(s, {}).get("mid") or 0) for s in long_symbols
        )
        current_value = round(short_value - long_value, 4)
        original_credit = spread.get("entry_credit", 0)
        pnl_pct = (
            round(((original_credit - current_value) / original_credit) * 100, 1)
            if original_credit > 0 else 0
        )

        # DTE
        dte_remaining = None
        try:
            exp = datetime.strptime(spread["expiration"], "%Y-%m-%d").date()
            dte_remaining = (exp - datetime.now().date()).days
        except (KeyError, ValueError):
            pass

        # Short call strike breach
        underlying_price = (context.get("technicals") or {}).get("current_price")
        short_call_strike = None
        for l in legs:
            if l.get("side", "").lower() == "sell":
                try:
                    short_call_strike = int(l["symbol"][-8:]) / 1000
                except (ValueError, IndexError):
                    pass
                break

        breached = (
            underlying_price is not None
            and short_call_strike is not None
            and underlying_price > short_call_strike
        )

        # Ex-dividend early assignment risk
        fund = context.get("fundamentals") or {}
        days_ex = fund.get("days_to_ex_dividend")
        if (
            days_ex is not None
            and dte_remaining is not None
            and days_ex <= dte_remaining
            and breached
        ):
            return {
                "action": "CLOSE",
                "reasoning": (
                    f"Ex-dividend in {days_ex} days with short call ITM — "
                    f"early assignment risk"
                ),
                "urgency": "immediate",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        # Standard exit conditions
        if pnl_pct >= 50:
            return {
                "action": "CLOSE",
                "reasoning": f"Captured {pnl_pct}% of max profit (>= 50% target)",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        if dte_remaining is not None and dte_remaining <= 10:
            return {
                "action": "CLOSE",
                "reasoning": f"DTE {dte_remaining} <= 10 (gamma risk)",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        if breached and dte_remaining is not None and dte_remaining < 15:
            return {
                "action": "CLOSE",
                "reasoning": f"Short call breached with DTE {dte_remaining} < 15",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        return {
            "action": "HOLD",
            "reasoning": f"Position OK. P&L: {pnl_pct}%, DTE: {dte_remaining}",
        }

    # ── execution ───────────────────────────────────────────

    def execute_entry(self, decision: dict) -> bool:
        legs = [
            {"symbol": decision["short_call_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": decision["long_call_symbol"], "side": "buy",
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
            logger.exception("Failed to place bear call spread order")
            return False

        order_id = order.get("id")
        logger.info("Bear call spread order placed: %s", order_id)

        if self.spread_tracker:
            spread_id = self.spread_tracker.register_spread(
                strategy_type="bear_call_spread",
                underlying=decision.get("underlying", ""),
                legs=legs,
                entry_credit=abs(decision.get("net_credit", 0)),
                entry_date=datetime.now().date().isoformat(),
                expiration=decision.get("expiration", ""),
                max_loss=decision.get("max_loss", 0),
                max_gain=abs(decision.get("net_credit", 0)) * 100,
                entry_order_id=order_id,
                cb_status_at_entry=self.cb_status_at_entry,
            )
            self.open_spread_id = spread_id

        self.pending_order_id = order_id
        self.state = BearCallSpreadState.PENDING_OPEN
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
                logger.warning("Failed to write bear call spread decision snapshot")

        return True

    def execute_exit(self, spread_id: str, limit_price: float | None = None) -> bool:
        if not self.spread_tracker:
            logger.error("No spread tracker — cannot close")
            return False

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="bear_call_spread",
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
            logger.exception("Failed to close bear call spread %s", spread_id)
            return False

        close_order_id = (close_order or {}).get("id")
        self.spread_tracker.mark_pending_close(spread_id, close_order_id)
        self.pending_order_id = close_order_id
        self.state = BearCallSpreadState.PENDING_CLOSE
        self._save_state()
        logger.info(
            "Bear call spread %s close submitted: %s (PENDING_CLOSE)",
            spread_id, close_order_id,
        )
        return True

