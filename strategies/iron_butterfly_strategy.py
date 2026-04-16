"""Iron Butterfly strategy — 4-leg defined-risk credit spread.

Sell ATM put + sell ATM call at the same center strike, buy OTM put
wing below + buy OTM call wing above, all same expiration.

Structurally identical to the iron condor except both short options
share the same ATM strike (center strike). Higher premium than the
condor but a narrower profit zone.

Valid as a single mleg order because the buy_to_open wings cover
both sell_to_open legs within the same order.

State persisted in ``settings.SNAPSHOTS_DIR / "iron_butterfly_state.json"``.
"""

import json
import logging
from datetime import datetime
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)


class IronButterflyState(str, Enum):
    IDLE = "IDLE"
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"


class IronButterflyStrategy:
    """State machine for the iron butterfly strategy."""

    State = IronButterflyState

    def __init__(self, broker, state_writer=None, spread_tracker=None, recorder=None):
        self.broker = broker
        self.state_writer = state_writer
        self.spread_tracker = spread_tracker
        self.recorder = recorder
        self.cb_status_at_entry: str | None = None
        self.state = IronButterflyState.IDLE
        self.open_spread_id: str | None = None
        self.pending_order_id: str | None = None

        self._state_path = settings.SNAPSHOTS_DIR / "iron_butterfly_state.json"
        self._load_state()

    # ── state persistence ───────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.state = IronButterflyState(data.get("state", "IDLE"))
            self.open_spread_id = data.get("open_spread_id")
            self.pending_order_id = data.get("pending_order_id")
        except Exception:
            logger.exception("Failed to load iron butterfly state — starting IDLE")

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

    def get_state(self) -> IronButterflyState:
        return self.state

    # ── main cycle ──────────────────────────────────────────

    def run_cycle(self, context: dict, advisor) -> dict:
        """Main decision method called by the scheduler."""
        if advisor is None:
            raise ValueError("advisor is required")
        if self.state in (
            IronButterflyState.PENDING_OPEN, IronButterflyState.PENDING_CLOSE,
        ):
            return {
                "action": "HOLD",
                "reasoning": f"Spread state {self.state.value} — awaiting fill confirmation",
            }
        if self.state == IronButterflyState.IDLE:
            return self._evaluate_entry(context, advisor=advisor)
        return self._evaluate_management(context)

    # ── entry evaluation ────────────────────────────────────

    def _evaluate_entry(self, context: dict, advisor=None) -> dict:
        """Check hard conditions, then ask Claude if they pass."""
        skip = self._check_entry_conditions(context)
        if skip:
            return {"action": "SKIP", "reasoning": skip, "skip_reason": skip}

        # Get iron butterfly candidate from context
        spread_candidates = context.get("spread_candidates", {})
        ib_data = spread_candidates.get("iron_butterfly", {})
        ib_legs = ib_data.get("iron_butterfly_legs")
        if not ib_legs:
            return {
                "action": "SKIP",
                "reasoning": "No viable iron butterfly candidates found",
                "skip_reason": "no_candidates",
            }

        enriched = {**context, "iron_butterfly_candidate": ib_legs}
        return advisor.ask_spread(enriched, "iron_butterfly", "idle")

    def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
        """Cheap, Claude-free pre-check used to rank candidates across symbols.

        Returns ``(skip_reason, score)``. Score is the combined butterfly credit.
        """
        skip = self._check_entry_conditions(context)
        if skip:
            return skip, 0.0

        spread_candidates = context.get("spread_candidates", {})
        ib_data = spread_candidates.get("iron_butterfly", {})
        ib_legs = ib_data.get("iron_butterfly_legs")
        if not ib_legs:
            return "No viable iron butterfly candidates found", 0.0

        ev = ib_legs.get("total_ev_score")
        score = float(ev) if ev is not None else float(ib_legs.get("total_credit", 0))

        # IV overvaluation scoring bonus
        iv_ov = (context.get("volatility") or {}).get("iv_overvalued_label")
        if iv_ov == "OVERVALUED":
            score *= 1.20

        return None, score

    def _check_entry_conditions(self, context: dict) -> str | None:
        """Return a skip reason string, or None if all conditions pass."""
        regime = context.get("confirmed_market_regime", "NEUTRAL")
        if regime not in ("NEUTRAL",):
            return f"Market regime is {regime}, need NEUTRAL"

        macro = context.get("macro") or {}
        vix = macro.get("vix")

        # IV rank check
        ivr = context.get("iv_rank")
        if ivr is not None and ivr < 50:
            return f"IV rank {ivr} < 50 minimum for iron butterfly"

        # VIX range
        if vix is not None and (vix < 18 or vix > 35):
            return f"VIX {vix} outside 18-35 range"

        # Earnings proximity (30 days for butterfly, tighter than condor's 35)
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 30:
            return f"Earnings in {dte_earnings} days (need > 30)"

        # IV forecast check
        iv_ov = (context.get("volatility") or {}).get("iv_overvalued_label")
        if iv_ov == "UNDERVALUED":
            return "IV is UNDERVALUED per ORATS forecast — poor edge for iron butterfly"

        # Contango check
        contango_label = (context.get("volatility") or {}).get("contango_label")
        if contango_label == "BACKWARDATION":
            return "Term structure in BACKWARDATION — unfavorable for neutral strategy"

        # Already have an active butterfly (includes PENDING states)
        if self.spread_tracker:
            open_ib = self.spread_tracker.get_active_spreads(strategy_type="iron_butterfly")
            if open_ib:
                return "Already have an active iron butterfly"

        return None

    # ── management evaluation ───────────────────────────────

    def _evaluate_management(self, context: dict) -> dict:
        """Check exit conditions for the open butterfly."""
        if not self.open_spread_id or not self.spread_tracker:
            return {"action": "HOLD", "reasoning": "No spread tracker or spread_id"}

        open_spreads = self.spread_tracker.get_open_spreads(strategy_type="iron_butterfly")
        spread = None
        for s in open_spreads:
            if s["spread_id"] == self.open_spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Open spread %s not found — resetting to IDLE", self.open_spread_id)
            self.state = IronButterflyState.IDLE
            self.open_spread_id = None
            self._save_state()
            return {"action": "SKIP", "reasoning": "Spread not found, reset to IDLE"}

        # Calculate current position value
        legs = spread.get("legs", [])
        short_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "sell"]
        long_symbols = [l["symbol"] for l in legs if l.get("side", "").lower() == "buy"]
        all_symbols = short_symbols + long_symbols

        snapshots = {}
        try:
            snapshots = self.broker.get_option_snapshots(all_symbols, underlying=spread.get("underlying", ""))
        except Exception:
            logger.warning("Failed to fetch snapshots for butterfly management")

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

        # Current spread value = sum(short mids) - sum(long mids)
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

        # Check if underlying has moved beyond the wings
        underlying_price = (context.get("technicals") or {}).get("current_price")
        put_wing_strike = None
        call_wing_strike = None
        for l in legs:
            sym = l.get("symbol", "")
            side = l.get("side", "").lower()
            if side == "buy":
                try:
                    strike = int(sym[-8:]) / 1000
                except (ValueError, IndexError):
                    continue
                # Determine option type from OCC symbol
                for i, ch in enumerate(sym):
                    if ch.isdigit():
                        type_idx = i + 6
                        if type_idx < len(sym):
                            if sym[type_idx].upper() == "P":
                                if put_wing_strike is None or strike > put_wing_strike:
                                    put_wing_strike = strike  # highest put wing = closer to ATM
                            elif sym[type_idx].upper() == "C":
                                if call_wing_strike is None or strike < call_wing_strike:
                                    call_wing_strike = strike  # lowest call wing = closer to ATM
                        break

        # Stop loss (200% of credit = current_value >= 2× original_credit)
        if original_credit > 0 and current_value >= original_credit * 2.0:
            return {
                "action": "CLOSE",
                "reasoning": (
                    f"Stop loss triggered: current value {current_value:.2f} "
                    f">= 200% of original credit {original_credit:.2f}"
                ),
                "urgency": "immediate",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        # Vol-of-vol tightening — take profits sooner in volatile IV environments
        vov_label = (context.get("volatility") or {}).get("vol_of_vol_label")
        profit_target_pct = 40 if vov_label == "HIGH" else 50

        if pnl_pct >= profit_target_pct:
            return {
                "action": "CLOSE",
                "reasoning": (
                    f"Captured {pnl_pct}% of max profit "
                    f"(>= {profit_target_pct}% target"
                    + (" — tightened due to high vol-of-vol)" if vov_label == "HIGH" else ")")
                ),
                "urgency": "normal",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        if dte_remaining is not None and dte_remaining <= 7:
            return {
                "action": "CLOSE",
                "reasoning": f"DTE {dte_remaining} <= 7 (gamma risk on all 4 legs)",
                "urgency": "immediate",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        # Wing breach — underlying moved beyond a protective wing
        beyond_wings = False
        if underlying_price and put_wing_strike and underlying_price < put_wing_strike:
            beyond_wings = True
        if underlying_price and call_wing_strike and underlying_price > call_wing_strike:
            beyond_wings = True

        if beyond_wings:
            return {
                "action": "CLOSE",
                "reasoning": "Underlying moved beyond protective wings — closing to limit further loss",
                "urgency": "immediate",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        return {
            "action": "HOLD",
            "reasoning": f"Position OK. P&L: {pnl_pct}%, DTE: {dte_remaining}",
        }

    # ── execution ───────────────────────────────────────────

    def execute_entry(self, decision: dict) -> bool:
        """Place the 4-leg mleg order and register with spread_tracker.

        Leg order: buy put wing → sell ATM put → sell ATM call → buy call wing.
        Valid as single mleg because the buy_to_open wings cover both
        sell_to_open legs within the same order.
        """
        legs = [
            {"symbol": decision["put_long_symbol"], "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
            {"symbol": decision["put_short_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": decision["call_short_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": decision["call_long_symbol"], "side": "buy",
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
            logger.exception("Failed to place iron butterfly order")
            return False

        order_id = order.get("id")
        logger.info("Iron butterfly order placed: %s", order_id)

        # Register with spread tracker
        if self.spread_tracker:
            spread_id = self.spread_tracker.register_spread(
                strategy_type="iron_butterfly",
                underlying=decision.get("underlying", ""),
                legs=legs,
                entry_credit=abs(decision.get("total_credit", 0)),
                entry_date=datetime.now().date().isoformat(),
                expiration=decision.get("expiration", ""),
                max_loss=decision.get("max_loss", 0),
                max_gain=abs(decision.get("total_credit", 0)) * 100,
                entry_order_id=order_id,
                cb_status_at_entry=self.cb_status_at_entry,
            )
            self.open_spread_id = spread_id

        self.pending_order_id = order_id
        self.state = IronButterflyState.PENDING_OPEN
        self._save_state()

        # Write decision snapshot
        if self.state_writer:
            try:
                self.state_writer.write_decision(
                    decision_dict=decision,
                    reasoning=decision.get("reasoning", ""),
                    action_taken=True,
                    underlying=decision.get("underlying", ""),
                )
            except Exception:
                logger.warning("Failed to write iron butterfly decision snapshot")

        return True

    def execute_exit(self, spread_id: str, limit_price: float | None = None) -> bool:
        """Close all 4 legs via close_mleg_position."""
        if not self.spread_tracker:
            logger.error("No spread tracker — cannot close")
            return False

        open_spreads = self.spread_tracker.get_open_spreads(strategy_type="iron_butterfly")
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
            logger.exception("Failed to close iron butterfly %s", spread_id)
            return False

        close_order_id = (close_order or {}).get("id")
        self.spread_tracker.mark_pending_close(spread_id, close_order_id)
        self.pending_order_id = close_order_id
        self.state = IronButterflyState.PENDING_CLOSE
        self._save_state()
        logger.info(
            "Iron butterfly %s close submitted: %s (PENDING_CLOSE)",
            spread_id, close_order_id,
        )
        return True
