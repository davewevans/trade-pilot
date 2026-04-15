# NOT YET ACTIVE — requires account assignment in config
"""Short Strangle strategy — sell OTM put and OTM call, same underlying + expiration.

Undefined risk. Collects premium from both sides. Profits when the
underlying stays between the short strikes and IV contracts.

⚠ UNDEFINED RISK: There are no protective wings. Only deploy with
strict entry criteria and in confirmed neutral, high-IV environments.

State persisted in ``settings.SNAPSHOTS_DIR / "short_strangle_state.json"``.
"""

import json
import logging
from datetime import datetime
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)


class ShortStrangleState(str, Enum):
    IDLE = "IDLE"
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"


class ShortStrangleStrategy:
    """State machine for the short strangle strategy (INACTIVE — not yet routed)."""

    State = ShortStrangleState

    def __init__(self, broker, state_writer=None, spread_tracker=None, recorder=None):
        self.broker = broker
        self.state_writer = state_writer
        self.spread_tracker = spread_tracker
        self.recorder = recorder
        self.cb_status_at_entry: str | None = None
        self.state = ShortStrangleState.IDLE
        self.open_spread_id: str | None = None
        self.pending_order_id: str | None = None

        self._state_path = settings.SNAPSHOTS_DIR / "short_strangle_state.json"
        self._load_state()

    # ── state persistence ───────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.state = ShortStrangleState(data.get("state", "IDLE"))
            self.open_spread_id = data.get("open_spread_id")
            self.pending_order_id = data.get("pending_order_id")
        except Exception:
            logger.exception("Failed to load short strangle state — starting IDLE")

    def _save_state(self) -> None:
        from utils.fileio import atomic_json_write
        atomic_json_write(self._state_path, {
            "state": self.state.value,
            "open_spread_id": self.open_spread_id,
            "pending_order_id": self.pending_order_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def reconcile_pending(self) -> None:
        from strategies._spread_lifecycle import reconcile_pending_state
        reconcile_pending_state(self)

    def get_state(self) -> ShortStrangleState:
        return self.state

    # ── main cycle ──────────────────────────────────────────

    def run_cycle(self, context: dict, advisor) -> dict:
        if advisor is None:
            raise ValueError("advisor is required")
        if self.state in (
            ShortStrangleState.PENDING_OPEN, ShortStrangleState.PENDING_CLOSE,
        ):
            return {
                "action": "HOLD",
                "reasoning": f"Strangle state {self.state.value} — awaiting fill confirmation",
            }
        if self.state == ShortStrangleState.IDLE:
            return self._evaluate_entry(context, advisor=advisor)
        return self._evaluate_management(context)

    # ── entry evaluation ────────────────────────────────────

    def _evaluate_entry(self, context: dict, advisor=None) -> dict:
        skip = self._check_entry_conditions(context)
        if skip:
            return {"action": "SKIP", "reasoning": skip, "skip_reason": skip}

        strangle_data = context.get("spread_candidates", {}).get("short_strangle", {})
        candidate = strangle_data.get("best_candidate")
        if not candidate:
            return {
                "action": "SKIP",
                "reasoning": "No viable short strangle candidates found",
                "skip_reason": "no_candidates",
            }

        enriched = {**context, "strangle_candidate": candidate}
        return advisor.ask_spread(enriched, "short_strangle", "idle")

    def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
        """Cheap, Claude-free pre-check. Returns (skip_reason, score)."""
        skip = self._check_entry_conditions(context)
        if skip:
            return skip, 0.0

        strangle_data = context.get("spread_candidates", {}).get("short_strangle", {})
        candidate = strangle_data.get("best_candidate")
        if not candidate:
            return "No viable short strangle candidates found", 0.0

        combined_credit = candidate.get("total_credit", 0)
        spread_yield = candidate.get("spread_yield", 0)
        if spread_yield < 0.003:
            return (
                f"Combined spread yield {spread_yield:.4f} < 0.003 minimum "
                f"(combined credit ${combined_credit} too thin)",
                0.0,
            )

        score = float(combined_credit)

        # IV overvaluation scoring
        iv_ov = (context.get("volatility") or {}).get("iv_overvalued_label")
        if iv_ov == "OVERVALUED":
            score *= 1.15

        return None, score

    def _check_entry_conditions(self, context: dict) -> str | None:
        """Return a skip reason or None if all conditions pass."""
        # Market regime must be NEUTRAL
        regime = context.get("confirmed_market_regime", "NEUTRAL")
        if regime != "NEUTRAL":
            return f"Market regime is {regime}, need NEUTRAL for short strangle"

        # IV must be HIGH
        ivr = context.get("iv_rank")
        if ivr is None or ivr < 50:
            return f"IV rank {ivr} < 50 — short strangle needs HIGH IV environment"

        vol = context.get("volatility") or {}

        # IV forecast check — never sell UNDERVALUED options
        iv_ov = vol.get("iv_overvalued_label")
        if iv_ov == "UNDERVALUED":
            return "IV is UNDERVALUED per ORATS forecast — poor edge for short strangle"

        # Contango check — backwardation indicates near-term stress
        contango_label = vol.get("contango_label")
        if contango_label == "BACKWARDATION":
            return "Term structure in BACKWARDATION — unfavorable for neutral undefined-risk strategy"

        # Earnings proximity — tighter than spreads due to undefined risk
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 35:
            return f"Earnings in {dte_earnings} days (need > 35 — undefined risk near earnings is reckless)"

        # Technical confirmation — range-bound behavior
        tech = context.get("technicals") or {}
        if tech.get("above_sma_50") is False:
            return "Underlying below 50-day SMA — not range-bound"

        # Already have an active strangle
        if self.spread_tracker:
            symbol = context.get("symbol", "")
            existing = self.spread_tracker.get_active_spreads(
                underlying=symbol, strategy_type="short_strangle",
            )
            if existing:
                return f"Already have an active short strangle on {symbol}"

        return None

    # ── management evaluation ───────────────────────────────

    def _evaluate_management(self, context: dict) -> dict:
        """Check exit conditions for the open strangle."""
        if not self.open_spread_id or not self.spread_tracker:
            return {"action": "HOLD", "reasoning": "No spread tracker or spread_id"}

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="short_strangle",
        )
        spread = None
        for s in open_spreads:
            if s["spread_id"] == self.open_spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Open strangle %s not found — resetting to IDLE", self.open_spread_id)
            self.state = ShortStrangleState.IDLE
            self.open_spread_id = None
            self._save_state()
            return {"action": "SKIP", "reasoning": "Strangle not found, reset to IDLE"}

        legs = spread.get("legs", [])
        short_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "sell"]

        all_symbols = short_symbols
        snapshots = {}
        try:
            snapshots = self.broker.get_option_snapshots(all_symbols)
        except Exception:
            logger.warning("Failed to fetch snapshots for strangle management")

        missing = [s for s in all_symbols if s not in snapshots or snapshots[s].get("mid") is None]
        if missing:
            return {
                "action": "HOLD",
                "reasoning": f"Cannot evaluate: missing price data for {len(missing)} leg(s)",
            }

        # Current combined value of the short legs
        current_value = round(
            sum((snapshots.get(s, {}).get("mid") or 0) for s in short_symbols), 4
        )
        original_credit = spread.get("entry_credit", 0)
        pnl_pct = (
            round(((original_credit - current_value) / original_credit) * 100, 1)
            if original_credit > 0 else 0
        )

        # DTE remaining
        dte_remaining = None
        try:
            exp = datetime.strptime(spread["expiration"], "%Y-%m-%d").date()
            dte_remaining = (exp - datetime.now().date()).days
        except (KeyError, ValueError):
            pass

        # Check delta breach on individual legs
        for sym in short_symbols:
            snap = snapshots.get(sym, {})
            delta = snap.get("delta")
            if delta is not None and abs(delta) > 0.40:
                return {
                    "action": "CLOSE",
                    "reasoning": (
                        f"Delta breach: {sym} delta {delta:.2f} exceeds 0.40 threshold "
                        f"(position is getting tested)"
                    ),
                    "spread_id": self.open_spread_id,
                    "limit_price": round(current_value, 2),
                }

        # Profit target: 50% of original credit
        if pnl_pct >= 50:
            return {
                "action": "CLOSE",
                "reasoning": f"Captured {pnl_pct}% of max profit (>= 50% target)",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        # Stop loss: current value >= 150% of original credit (tighter than IC — undefined risk)
        if original_credit > 0 and current_value >= original_credit * 1.5:
            return {
                "action": "CLOSE",
                "reasoning": (
                    f"Stop loss: current value ${current_value:.2f} >= 150% of "
                    f"entry credit ${original_credit:.2f}"
                ),
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        # DTE <= 10: gamma risk amplified without wing protection (exit earlier than IC)
        if dte_remaining is not None and dte_remaining <= 10:
            return {
                "action": "CLOSE",
                "reasoning": f"DTE {dte_remaining} <= 10 (gamma risk — no wing protection)",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        return {
            "action": "HOLD",
            "reasoning": f"Position OK. P&L: {pnl_pct}%, DTE: {dte_remaining}",
        }

    # ── execution ───────────────────────────────────────────

    def execute_entry(self, decision: dict) -> bool:
        """Place the 2-leg mleg order and register with spread_tracker."""
        legs = [
            {"symbol": decision["put_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": decision["call_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
        ]

        try:
            order = self.broker.place_mleg_order(
                legs=legs,
                order_type="limit",
                limit_price=decision["limit_price"],
                qty=1,
            )
        except Exception:
            logger.exception("Failed to place short strangle order")
            return False

        order_id = order.get("id")
        logger.info("Short strangle order placed: %s", order_id)

        if self.spread_tracker:
            spread_id = self.spread_tracker.register_spread(
                strategy_type="short_strangle",
                underlying=decision.get("underlying", ""),
                legs=legs,
                entry_credit=abs(decision.get("total_credit", 0)),
                entry_date=datetime.now().date().isoformat(),
                expiration=decision.get("expiration", ""),
                max_loss=None,  # undefined risk
                max_gain=abs(decision.get("total_credit", 0)) * 100,
                entry_order_id=order_id,
                cb_status_at_entry=self.cb_status_at_entry,
            )
            self.open_spread_id = spread_id

        self.pending_order_id = order_id
        self.state = ShortStrangleState.PENDING_OPEN
        self._save_state()
        return True

    def execute_exit(self, spread_id: str, limit_price: float | None = None) -> bool:
        """Close both short legs."""
        if not self.spread_tracker:
            logger.error("No spread tracker — cannot close strangle")
            return False

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="short_strangle",
        )
        spread = None
        for s in open_spreads:
            if s["spread_id"] == spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Strangle %s not found for exit", spread_id)
            return False

        try:
            close_order = self.broker.close_mleg_position(
                open_legs=spread["legs"],
                order_type="limit" if limit_price else "market",
                limit_price=limit_price,
                qty=1,
            )
        except Exception:
            logger.exception("Failed to close short strangle %s", spread_id)
            return False

        close_order_id = (close_order or {}).get("id")
        self.spread_tracker.mark_pending_close(spread_id, close_order_id)

        self.pending_order_id = close_order_id
        self.state = ShortStrangleState.PENDING_CLOSE
        self._save_state()
        logger.info(
            "Short strangle %s close submitted: %s (PENDING_CLOSE)",
            spread_id, close_order_id,
        )
        return True
