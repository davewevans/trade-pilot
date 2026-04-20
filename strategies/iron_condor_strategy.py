"""Iron Condor strategy — 4-leg credit spread that profits in range-bound markets.

Sell OTM put + buy lower put + sell OTM call + buy higher call, all same
expiration.  One condor position at a time per underlying.

State persisted in ``settings.SNAPSHOTS_DIR / "iron_condor_state.json"``.
"""

import json
import logging
from datetime import datetime
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)

_IC_ENTRY_FIELDS = {
    "action", "reasoning",
}
_IC_MGMT_FIELDS = {
    "action", "reasoning",
}


class IronCondorState(str, Enum):
    IDLE = "IDLE"
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"


class IronCondorStrategy:
    """State machine for the iron condor strategy."""

    State = IronCondorState
    STRATEGY_TYPE = "iron_condor"

    def __init__(self, broker, state_writer=None, spread_tracker=None, recorder=None,
                 liquidity_repo=None, backtest_stats_repo=None):
        self.broker = broker
        self.state_writer = state_writer
        self.spread_tracker = spread_tracker
        self.recorder = recorder
        self._liquidity_repo = liquidity_repo
        self._backtest_stats_repo = backtest_stats_repo
        self.state = IronCondorState.IDLE
        self.open_spread_id: str | None = None
        self.pending_order_id: str | None = None

        self._state_path = settings.SNAPSHOTS_DIR / "iron_condor_state.json"
        self._load_state()

    # ── state persistence ───────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.state = IronCondorState(data.get("state", "IDLE"))
            self.open_spread_id = data.get("open_spread_id")
            self.pending_order_id = data.get("pending_order_id")
        except Exception:
            logger.exception("Failed to load iron condor state — starting IDLE")

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

    def get_state(self) -> IronCondorState:
        return self.state

    # ── main cycle ──────────────────────────────────────────

    def run_cycle(self, context: dict, advisor) -> dict:
        """Main decision method called by the scheduler."""
        if advisor is None:
            raise ValueError("advisor is required")
        if self.state in (
            IronCondorState.PENDING_OPEN, IronCondorState.PENDING_CLOSE,
        ):
            return {
                "action": "HOLD",
                "reasoning": f"Spread state {self.state.value} — awaiting fill confirmation",
            }
        if self.state == IronCondorState.IDLE:
            return self._evaluate_entry(context, advisor=advisor)
        return self._evaluate_management(context)

    # ── entry evaluation ────────────────────────────────────

    def _evaluate_entry(self, context: dict, advisor=None) -> dict:
        """Check hard conditions, then ask Claude if they pass."""
        skip = self._check_entry_conditions(context)
        if skip:
            return {"action": "SKIP", "reasoning": skip, "skip_reason": skip}

        # Get iron condor candidate from context
        spread_candidates = context.get("spread_candidates", {})
        ic_data = spread_candidates.get("iron_condor", {})
        ic_legs = ic_data.get("iron_condor_legs")
        if not ic_legs:
            return {
                "action": "SKIP",
                "reasoning": "No viable iron condor candidates found",
                "skip_reason": "no_candidates",
            }

        enriched = {**context, "iron_condor_candidate": ic_legs}
        return advisor.ask_spread(enriched, "iron_condor", "idle")

    def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
        """Cheap, Claude-free pre-check used to rank candidates across symbols.

        Returns ``(skip_reason, score)``. Score is the combined condor credit.
        """
        skip = self._check_entry_conditions(context)
        if skip:
            return skip, 0.0

        spread_candidates = context.get("spread_candidates", {})
        ic_data = spread_candidates.get("iron_condor", {})
        ic_legs = ic_data.get("iron_condor_legs")
        if not ic_legs:
            return "No viable iron condor candidates found", 0.0

        # Rank by combined EV score (ORATS-adjusted POP × max_gain - loss risk,
        # summed across both legs).  Falls back to total_credit when unavailable.
        ev = ic_legs.get("total_ev_score")
        score = float(ev) if ev is not None else float(ic_legs.get("total_credit", 0))

        # IV overvaluation scoring bonus — stronger for 4-leg (more vega exposure)
        iv_ov = (context.get("volatility") or {}).get("iv_overvalued_label")
        if iv_ov == "OVERVALUED":
            score *= 1.20

        # ── Liquidity multiplier ──────────────────────────────────────────────
        symbol = context.get("symbol", "")
        if self._liquidity_repo is not None and symbol:
            try:
                liq_mult, liq_tier, liq_conf = self._liquidity_repo.get_multiplier(
                    symbol, self.STRATEGY_TYPE,
                )
            except Exception:
                logger.warning(
                    "Liquidity multiplier lookup failed for %s/%s",
                    symbol, self.STRATEGY_TYPE, exc_info=True,
                )
                liq_mult, liq_tier, liq_conf = (1.0, "B", "none")
        else:
            liq_mult, liq_tier, liq_conf = (1.0, "B", "disabled")

        if liq_mult == 0.0:
            return "below_liquidity_floor", 0.0

        # ── Win-rate multiplier ───────────────────────────────────────────────
        if self._backtest_stats_repo is not None and symbol:
            try:
                wr_mult, wr_tier, wr_conf = self._backtest_stats_repo.get_winrate_multiplier(
                    symbol, self.STRATEGY_TYPE,
                )
            except Exception:
                logger.warning(
                    "Win-rate multiplier lookup failed for %s/%s",
                    symbol, self.STRATEGY_TYPE, exc_info=True,
                )
                wr_mult, wr_tier, wr_conf = (1.0, "neutral", "none")
        else:
            wr_mult, wr_tier, wr_conf = (1.0, "neutral", "disabled")

        if wr_mult == 0.0:
            return "below_winrate_floor", 0.0

        combined_multiplier = liq_mult * wr_mult
        final_score = score * combined_multiplier

        research_meta = context.setdefault("_research", {})
        research_meta["liquidity"] = {"multiplier": liq_mult, "tier": liq_tier, "confidence": liq_conf}
        research_meta["winrate"] = {"multiplier": wr_mult, "tier": wr_tier, "confidence": wr_conf}
        research_meta["combined_multiplier"] = combined_multiplier
        research_meta["final_score"] = final_score

        return None, final_score

    def _check_entry_conditions(self, context: dict) -> str | None:
        """Return a skip reason string, or None if all conditions pass."""
        regime = context.get("confirmed_market_regime", "NEUTRAL")
        if regime not in ("NEUTRAL",):
            return f"Market regime is {regime}, need NEUTRAL"

        iv_env = context.get("iv_environment", "MODERATE")
        macro = context.get("macro") or {}
        vix = macro.get("vix")

        # IV rank check
        ivr = context.get("iv_rank")
        if ivr is not None and ivr < 50:
            return f"IV rank {ivr} < 50 minimum for iron condor"

        # VIX range
        if vix is not None and (vix < 18 or vix > 35):
            return f"VIX {vix} outside 18-35 range"

        # Earnings proximity
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 35:
            return f"Earnings in {dte_earnings} days (need > 35)"

        # IV forecast check — poor time to sell if IV is undervalued
        iv_ov = (context.get("volatility") or {}).get("iv_overvalued_label")
        if iv_ov == "UNDERVALUED":
            return "IV is UNDERVALUED per ORATS forecast — poor edge for iron condor"

        # Contango check — backwardation undermines the neutral thesis
        contango_label = (context.get("volatility") or {}).get("contango_label")
        if contango_label == "BACKWARDATION":
            return "Term structure in BACKWARDATION — unfavorable for neutral strategy"

        # Already have an active condor (includes PENDING states).
        if self.spread_tracker:
            open_ic = self.spread_tracker.get_active_spreads(strategy_type="iron_condor")
            if open_ic:
                return "Already have an active iron condor"

        return None

    # ── management evaluation ───────────────────────────────

    def _evaluate_management(self, context: dict) -> dict:
        """Check exit conditions for the open condor."""
        if not self.open_spread_id or not self.spread_tracker:
            return {"action": "HOLD", "reasoning": "No spread tracker or spread_id"}

        open_spreads = self.spread_tracker.get_open_spreads(strategy_type="iron_condor")
        spread = None
        for s in open_spreads:
            if s["spread_id"] == self.open_spread_id:
                spread = s
                break

        if not spread:
            logger.warning("Open spread %s not found — resetting to IDLE", self.open_spread_id)
            self.state = IronCondorState.IDLE
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
            logger.warning("Failed to fetch snapshots for condor management")

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

        # Check short strike breaches
        underlying_price = (context.get("technicals") or {}).get("current_price")
        short_put_strike = None
        short_call_strike = None
        for l in legs:
            sym = l.get("symbol", "")
            side = l.get("side", "").lower()
            if side == "sell":
                try:
                    strike = int(sym[-8:]) / 1000
                except (ValueError, IndexError):
                    continue
                # Determine type from OCC
                for i, ch in enumerate(sym):
                    if ch.isdigit():
                        type_idx = i + 6
                        if type_idx < len(sym):
                            if sym[type_idx].upper() == "P":
                                short_put_strike = strike
                            elif sym[type_idx].upper() == "C":
                                short_call_strike = strike
                        break

        breached = False
        if underlying_price and short_put_strike and underlying_price < short_put_strike:
            breached = True
        if underlying_price and short_call_strike and underlying_price > short_call_strike:
            breached = True

        # Hard exit conditions
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
                "urgency": "normal",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        if dte_remaining is not None and dte_remaining <= 10:
            return {
                "action": "CLOSE",
                "reasoning": f"DTE {dte_remaining} <= 10 (gamma risk)",
                "urgency": "immediate",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        if breached and dte_remaining is not None and dte_remaining <= 20:
            return {
                "action": "CLOSE",
                "reasoning": f"Short strike breached with DTE {dte_remaining} <= 20",
                "urgency": "immediate",
                "spread_id": self.open_spread_id,
                "limit_price": round(current_value, 2),
            }

        if breached and dte_remaining is not None and dte_remaining > 20:
            return {
                "action": "HOLD",
                "reasoning": f"Short strike breached but DTE {dte_remaining} > 20 — letting theta work",
            }

        return {
            "action": "HOLD",
            "reasoning": f"Position OK. P&L: {pnl_pct}%, DTE: {dte_remaining}",
        }

    # ── execution ───────────────────────────────────────────

    def execute_entry(self, decision: dict, cb_status: str | None = None) -> bool:
        """Place the 4-leg mleg order and register with spread_tracker."""
        legs = [
            {"symbol": decision["put_short_symbol"], "side": "sell",
             "ratio_qty": 1, "position_intent": "sell_to_open"},
            {"symbol": decision["put_long_symbol"], "side": "buy",
             "ratio_qty": 1, "position_intent": "buy_to_open"},
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
            logger.exception("Failed to place iron condor order")
            return False

        order_id = order.get("id")
        logger.info("Iron condor order placed: %s", order_id)

        if order_id:
            try:
                from data.shadow_execution import record_submission as _shadow_record
                from utils.occ import parse_occ as _parse_occ
                _shadow_legs = []
                for _leg in legs:
                    _parsed = _parse_occ(_leg["symbol"])
                    _opt = _parsed["option_type"].lower() if _parsed else "unknown"
                    _short = _leg["position_intent"] in ("sell_to_open", "buy_to_close")
                    _shadow_legs.append({
                        "contract_symbol": _leg["symbol"],
                        "leg_role": f"{'short' if _short else 'long'}_{_opt}",
                        "side": _leg["side"],
                        "position_intent": _leg["position_intent"],
                    })
                _shadow_record(
                    strategy_type="iron_condor",
                    action="open_spread",
                    legs=_shadow_legs,
                    net_limit_price=decision["limit_price"],
                    alpaca_order_id=order_id,
                )
            except Exception:
                logger.debug("shadow_execution recording failed (non-fatal)", exc_info=True)

        # Register with spread tracker
        if self.spread_tracker:
            spread_id = self.spread_tracker.register_spread(
                strategy_type="iron_condor",
                underlying=decision.get("underlying", ""),
                legs=legs,
                entry_credit=abs(decision.get("total_credit", 0)),
                entry_date=datetime.now().date().isoformat(),
                expiration=decision.get("expiration", ""),
                max_loss=decision.get("max_loss", 0),
                max_gain=abs(decision.get("total_credit", 0)) * 100,
                entry_order_id=order_id,
                cb_status_at_entry=cb_status,
            )
            self.open_spread_id = spread_id

        self.pending_order_id = order_id
        self.state = IronCondorState.PENDING_OPEN
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
                logger.warning("Failed to write iron condor decision snapshot")

        return True

    def execute_exit(self, spread_id: str, limit_price: float | None = None) -> bool:
        """Close all 4 legs via close_mleg_position."""
        if not self.spread_tracker:
            logger.error("No spread tracker — cannot close")
            return False

        open_spreads = self.spread_tracker.get_open_spreads(strategy_type="iron_condor")
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
            logger.exception("Failed to close iron condor %s", spread_id)
            return False

        close_order_id = (close_order or {}).get("id")

        if close_order_id and limit_price is not None:
            try:
                from data.shadow_execution import record_submission as _shadow_record
                from utils.occ import parse_occ as _parse_occ
                _intent_map = {"sell_to_open": "buy_to_close", "buy_to_open": "sell_to_close"}
                _side_map = {"sell": "buy", "buy": "sell"}
                _shadow_legs = []
                for _leg in spread["legs"]:
                    _parsed = _parse_occ(_leg["symbol"])
                    _opt = _parsed["option_type"].lower() if _parsed else "unknown"
                    _intent = _intent_map.get(_leg["position_intent"], _leg["position_intent"])
                    _short = _intent in ("sell_to_open", "buy_to_close")
                    _shadow_legs.append({
                        "contract_symbol": _leg["symbol"],
                        "leg_role": f"{'short' if _short else 'long'}_{_opt}",
                        "side": _side_map.get(_leg["side"], _leg["side"]),
                        "position_intent": _intent,
                    })
                _shadow_record(
                    strategy_type="iron_condor",
                    action="close_spread",
                    legs=_shadow_legs,
                    net_limit_price=limit_price,
                    alpaca_order_id=close_order_id,
                )
            except Exception:
                logger.debug("shadow_execution recording failed (non-fatal)", exc_info=True)

        self.spread_tracker.mark_pending_close(spread_id, close_order_id)
        self.pending_order_id = close_order_id
        self.state = IronCondorState.PENDING_CLOSE
        self._save_state()
        logger.info(
            "Iron condor %s close submitted: %s (PENDING_CLOSE)",
            spread_id, close_order_id,
        )
        return True

