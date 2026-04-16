"""Bull Put Spread strategy — defined-risk credit spread on bullish/neutral stocks.

Sell OTM put + buy further OTM put at a lower strike, same expiration.
Collects net credit; max loss capped by the long put.

One spread at a time per underlying.
State persisted in ``settings.SNAPSHOTS_DIR / "bull_put_spread_state.json"``.
"""

import json
import logging
from datetime import datetime
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)


class BullPutSpreadState(str, Enum):
    IDLE = "IDLE"
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"


class BullPutSpreadStrategy:
    """State machine for the bull put spread strategy."""

    State = BullPutSpreadState  # exposed for shared lifecycle helper

    def __init__(self, broker, state_writer=None, spread_tracker=None, recorder=None):
        self.broker = broker
        self.state_writer = state_writer
        self.spread_tracker = spread_tracker
        self.recorder = recorder  # TradeRecorder for DB dual-write; may be None
        # Set by the orchestrator (market_open) before each cycle so the
        # tracker can persist the CB status that was active at entry.
        self.cb_status_at_entry: str | None = None
        self.state = BullPutSpreadState.IDLE
        self.open_spread_id: str | None = None
        self.pending_order_id: str | None = None

        self._state_path = settings.SNAPSHOTS_DIR / "bull_put_spread_state.json"
        self._load_state()

    # ── state persistence ───────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.state = BullPutSpreadState(data.get("state", "IDLE"))
            self.open_spread_id = data.get("open_spread_id")
            self.pending_order_id = data.get("pending_order_id")
        except Exception:
            logger.exception("Failed to load bull put spread state — starting IDLE")

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

    def get_state(self) -> BullPutSpreadState:
        return self.state

    # ── main cycle ──────────────────────────────────────────

    def run_cycle(self, context: dict, advisor) -> dict:
        """Main decision method called by the scheduler."""
        if advisor is None:
            raise ValueError("advisor is required")
        # Skip while an entry/close order is still pending fill — acting
        # now would mean acting on a phantom (or already-closing) position.
        if self.state in (
            BullPutSpreadState.PENDING_OPEN, BullPutSpreadState.PENDING_CLOSE,
        ):
            return {
                "action": "HOLD",
                "reasoning": f"Spread state {self.state.value} — awaiting fill confirmation",
            }
        if self.state == BullPutSpreadState.IDLE:
            return self._evaluate_entry(context, advisor=advisor)
        return self._evaluate_management(context)

    # ── entry evaluation ────────────────────────────────────

    def _evaluate_entry(self, context: dict, advisor=None) -> dict:
        """Check hard conditions, then ask Claude if they pass."""
        skip = self._check_entry_conditions(context)
        if skip:
            return {"action": "SKIP", "reasoning": skip, "skip_reason": skip}

        # Get bull put spread candidate from context
        spread_candidates = context.get("spread_candidates", {})
        bps_data = spread_candidates.get("bull_put_spread", {})
        best = bps_data.get("best_candidate")
        if not best:
            return {
                "action": "SKIP",
                "reasoning": "No viable bull put spread candidates found",
                "skip_reason": "no_candidates",
            }

        # Check candidate quality — spread yield check (ORATS methodology)
        spread_yield = best.get("spread_yield", 0)
        net_credit = best.get("net_credit", 0)
        if spread_yield < 0.001:
            return {
                "action": "SKIP",
                "reasoning": (
                    f"Spread yield {spread_yield:.4f} < 0.001 minimum "
                    f"(credit ${net_credit} too thin for stock price)"
                ),
                "skip_reason": "low_spread_yield",
            }
        if net_credit < 0.30:
            return {
                "action": "SKIP",
                "reasoning": f"Absolute credit ${net_credit} < $0.30 minimum",
                "skip_reason": "low_credit",
            }
        if best.get("credit_to_width_ratio", 0) < 0.15:
            return {
                "action": "SKIP",
                "reasoning": f"Credit/width ratio {best.get('credit_to_width_ratio', 0)} < 0.15",
                "skip_reason": "low_ratio",
            }

        enriched = {**context, "best_candidate": best}
        return advisor.ask_spread(enriched, "bull_put_spread", "idle")

    def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
        """Cheap, Claude-free pre-check used to rank candidates across symbols.

        Runs hard skip conditions and candidate-quality checks; returns
        ``(skip_reason, score)``. ``skip_reason`` is None when this symbol is
        a viable candidate. ``score`` is the candidate's net credit (higher
        is better). Used by ``market_open`` to pick the single best symbol
        before paying for a Claude call.
        """
        skip = self._check_entry_conditions(context)
        if skip:
            return skip, 0.0

        spread_candidates = context.get("spread_candidates", {})
        bps_data = spread_candidates.get("bull_put_spread", {})
        best = bps_data.get("best_candidate")
        if not best:
            return "No viable bull put spread candidates found", 0.0

        net_credit = best.get("net_credit", 0)
        spread_yield = best.get("spread_yield", 0)
        if spread_yield < 0.001:
            return (
                f"Spread yield {spread_yield:.4f} < 0.001 minimum "
                f"(credit ${net_credit} too thin for stock price)",
                0.0,
            )
        if net_credit < 0.30:
            return f"Absolute credit ${net_credit} < $0.30 minimum", 0.0
        if best.get("credit_to_width_ratio", 0) < 0.15:
            return (
                f"Credit/width ratio {best.get('credit_to_width_ratio', 0)} < 0.15",
                0.0,
            )

        # Rank by EV score (ORATS-adjusted probability × max_gain - loss risk).
        # Falls back to net_credit when EV data is unavailable.
        ev = best.get("ev_score")
        score = float(ev) if ev is not None else float(net_credit)

        # IV overvaluation scoring bonus/penalty
        iv_ov_label = (context.get("volatility") or {}).get("iv_overvalued_label")
        if iv_ov_label == "OVERVALUED":
            score *= 1.15  # 15% bonus — selling edge confirmed by ORATS forecast
        elif iv_ov_label == "UNDERVALUED":
            score *= 0.85  # 15% penalty — IV may be cheap, less edge

        return None, score

    def _check_entry_conditions(self, context: dict) -> str | None:
        """Return a skip reason string, or None if all conditions pass."""
        regime = context.get("confirmed_market_regime", "NEUTRAL")
        if regime not in ("NEUTRAL", "BULL"):
            return f"Market regime is {regime}, need NEUTRAL or BULL"

        ivr = context.get("iv_rank")
        if ivr is not None and ivr < 35:
            return f"IV rank {ivr} < 35 minimum"

        tech = context.get("technicals") or {}
        if tech.get("above_sma_50") is False:
            return "Underlying below 50-day SMA"

        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 25:
            return f"Earnings in {dte_earnings} days (need > 25)"

        iv_hv = (context.get("volatility") or {}).get("iv_hv_ratio")
        if iv_hv is not None and iv_hv < 0.90:
            return (
                f"IV/HV ratio {iv_hv:.2f} < 0.90 — options underpriced for premium selling"
            )

        # Already have an active spread on same underlying (includes
        # PENDING_OPEN / PENDING_CLOSE — must not double-submit).
        if self.spread_tracker:
            symbol = context.get("symbol", "")
            open_bps = self.spread_tracker.get_active_spreads(
                underlying=symbol, strategy_type="bull_put_spread",
            )
            if open_bps:
                return f"Already have an active bull put spread on {symbol}"

        return None

    # ── management evaluation ───────────────────────────────

    def _evaluate_management(self, context: dict) -> dict:
        """Check exit conditions for the open spread."""
        if not self.open_spread_id or not self.spread_tracker:
            return {"action": "HOLD", "reasoning": "No spread tracker or spread_id"}

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="bull_put_spread",
        )
        spread = None
        for s in open_spreads:
            if s["spread_id"] == self.open_spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Open spread %s not found — resetting to IDLE", self.open_spread_id)
            self.state = BullPutSpreadState.IDLE
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
            snapshots = self.broker.get_option_snapshots(all_symbols, underlying=spread.get("underlying", ""))
        except Exception:
            logger.warning("Failed to fetch snapshots for bull put spread management")

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

        # Short put strike breach
        underlying_price = (context.get("technicals") or {}).get("current_price")
        short_put_strike = None
        for l in legs:
            if l.get("side", "").lower() == "sell":
                try:
                    short_put_strike = int(l["symbol"][-8:]) / 1000
                except (ValueError, IndexError):
                    pass
                break

        breached = (
            underlying_price is not None
            and short_put_strike is not None
            and underlying_price < short_put_strike
        )

        # Exit conditions
        # When vol_of_vol is HIGH, option prices whip around intraday —
        # take profits sooner (40%) before they evaporate.
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
                "reasoning": f"Short put breached with DTE {dte_remaining} < 15",
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
            {"symbol": decision["short_put_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": decision["long_put_symbol"], "side": "buy",
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
            logger.exception("Failed to place bull put spread order")
            return False

        order_id = order.get("id")
        logger.info("Bull put spread order placed: %s", order_id)

        if self.spread_tracker:
            spread_id = self.spread_tracker.register_spread(
                strategy_type="bull_put_spread",
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

        # PENDING_OPEN until reconciliation confirms the entry filled.
        self.pending_order_id = order_id
        self.state = BullPutSpreadState.PENDING_OPEN
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
                logger.warning("Failed to write bull put spread decision snapshot")

        return True

    def execute_exit(self, spread_id: str, limit_price: float | None = None) -> bool:
        """Close both legs via close_mleg_position."""
        if not self.spread_tracker:
            logger.error("No spread tracker — cannot close")
            return False

        open_spreads = self.spread_tracker.get_open_spreads(
            strategy_type="bull_put_spread",
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
            logger.exception("Failed to close bull put spread %s", spread_id)
            return False

        close_order_id = (close_order or {}).get("id")
        # PENDING_CLOSE until reconciliation confirms the close filled.
        self.spread_tracker.mark_pending_close(spread_id, close_order_id)

        self.pending_order_id = close_order_id
        self.state = BullPutSpreadState.PENDING_CLOSE
        self._save_state()
        logger.info(
            "Bull put spread %s close submitted: %s (PENDING_CLOSE)",
            spread_id, close_order_id,
        )
        return True

