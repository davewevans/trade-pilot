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

from config import settings

logger = logging.getLogger(__name__)


class LongCallVerticalState(str, Enum):
    IDLE = "IDLE"
    PENDING_OPEN = "PENDING_OPEN"
    OPEN = "OPEN"
    PENDING_CLOSE = "PENDING_CLOSE"


class LongCallVerticalStrategy:
    """State machine for the long call vertical (bull call debit spread)."""

    State = LongCallVerticalState
    STRATEGY_TYPE = "long_call_vertical"

    def __init__(self, broker, state_writer=None, spread_tracker=None, recorder=None,
                 liquidity_repo=None, backtest_stats_repo=None):
        self.broker = broker
        self.state_writer = state_writer
        self.spread_tracker = spread_tracker
        self.recorder = recorder
        self._liquidity_repo = liquidity_repo
        self._backtest_stats_repo = backtest_stats_repo
        self.state = LongCallVerticalState.IDLE
        self.open_spread_id: str | None = None
        self.pending_order_id: str | None = None

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

    def get_state(self) -> LongCallVerticalState:
        return self.state

    # ── main cycle ──────────────────────────────────────────

    def run_cycle(self, context: dict, advisor) -> dict:
        if advisor is None:
            raise ValueError("advisor is required")
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

        enriched = {**context, "best_candidate": best}
        return advisor.ask_spread(enriched, "long_call_vertical", "idle")

    def pre_check_entry(self, context: dict) -> tuple[str | None, float]:
        """Cheap, Claude-free pre-check used to rank candidates across symbols.

        Returns ``(skip_reason, score)``. For a debit spread we prefer the
        candidate with the best reward/risk ratio, so score = (max_gain /
        net_debit). Falls back to inverse of net_debit if max_gain missing.
        """
        skip = self._check_entry_conditions(context)
        if skip:
            return skip, 0.0

        spread_candidates = context.get("spread_candidates", {})
        lcv_data = spread_candidates.get("long_call_vertical", {})
        best = lcv_data.get("best_candidate")
        if not best:
            return "No viable long call vertical candidates found", 0.0

        net_debit = best.get("net_debit", 0) or 0
        if net_debit <= 0 or net_debit >= 2.00:
            return (
                f"Net debit ${net_debit} outside range (need $0.20-$2.00)",
                0.0,
            )

        # IV forecast check — buying overvalued options is poor value
        iv_ov = (context.get("volatility") or {}).get("iv_overvalued_label")
        if iv_ov == "OVERVALUED":
            return "IV is OVERVALUED per ORATS forecast — poor time to buy options", 0.0

        # Rank by EV score when available (accounts for whether call options
        # are cheap relative to ORATS' forecast — key for debit spreads where
        # overpriced IV hurts). Falls back to reward/risk ratio.
        ev = best.get("ev_score")
        if ev is not None:
            score = float(ev)
        else:
            max_gain = best.get("max_gain") or 0
            score = (float(max_gain) / float(net_debit)) if max_gain else (1.0 / float(net_debit))

        # Scoring bonus for buying cheap options, penalty for expensive
        if iv_ov == "UNDERVALUED":
            score *= 1.20  # Bonus — buying cheap options
        elif iv_ov == "OVERVALUED":
            score *= 0.70  # Strong penalty — buying expensive options

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
        regime = context.get("confirmed_market_regime", "NEUTRAL")
        if regime != "BULL":
            return f"Market regime is {regime}, need BULL"

        iv_env = context.get("iv_environment", "MODERATE")
        if iv_env != "LOW":
            return f"IV environment is {iv_env}, need LOW (don't buy expensive options)"

        ivr = context.get("iv_rank")
        if ivr is not None and ivr >= 30:
            return f"IV rank {ivr} >= 30 (need < 30 for debit spread)"

        iv_hv = (context.get("volatility") or {}).get("iv_hv_ratio")
        if iv_hv is not None and iv_hv > 1.30:
            return (
                f"IV/HV ratio {iv_hv:.2f} > 1.30 — options overpriced for debit strategy"
            )

        # IV forecast check — buying overvalued options is poor value
        iv_ov = (context.get("volatility") or {}).get("iv_overvalued_label")
        if iv_ov == "OVERVALUED":
            return "IV is OVERVALUED per ORATS forecast — poor time to buy options"

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

        all_symbols = buy_symbols + sell_symbols
        snapshots = {}
        try:
            snapshots = self.broker.get_option_snapshots(all_symbols, underlying=spread.get("underlying", ""))
        except Exception:
            logger.warning("Failed to fetch snapshots for long call vertical management")

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

        # 2. Proportional time exit: close when 60% of original DTE has
        #    elapsed without at least 25% of max gain (theta acceleration
        #    makes holding a debit spread past this point poor R/R).
        original_dte = spread.get("original_dte")
        if (
            dte_remaining is not None
            and original_dte
            and original_dte > 0
        ):
            dte_elapsed_pct = (
                (original_dte - dte_remaining) / original_dte * 100
            )
            if dte_elapsed_pct >= 60 and gain_pct < 25:
                return {
                    "action": "CLOSE",
                    "reasoning": (
                        f"{dte_elapsed_pct:.0f}% of DTE elapsed with only "
                        f"{gain_pct:.0f}% gain — theta working against us"
                    ),
                    "spread_id": self.open_spread_id,
                    "limit_price": round(current_value, 2),
                }
        # Hard floor: any spread within 10 days of expiration closes
        # regardless of original DTE (gamma risk).
        if dte_remaining is not None and dte_remaining <= 10:
            return {
                "action": "CLOSE",
                "reasoning": f"DTE {dte_remaining} <= 10 (gamma risk)",
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

    def execute_entry(self, decision: dict, cb_status: str | None = None) -> bool:
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
                    strategy_type="long_call_vertical",
                    action="open_spread",
                    legs=_shadow_legs,
                    net_limit_price=decision["limit_price"],
                    alpaca_order_id=order_id,
                )
            except Exception:
                logger.debug("shadow_execution recording failed (non-fatal)", exc_info=True)

        if self.spread_tracker:
            wing_width = decision.get("short_call_strike", 0) - decision.get("long_call_strike", 0)
            # Compute original DTE so management can apply a proportional
            # time-exit (close once 60% of DTE elapsed without 25% gain).
            original_dte = decision.get("dte")
            if original_dte is None:
                try:
                    exp = datetime.strptime(
                        decision.get("expiration", ""), "%Y-%m-%d",
                    ).date()
                    original_dte = (exp - datetime.now().date()).days
                except (ValueError, TypeError):
                    original_dte = None
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
                cb_status_at_entry=cb_status,
                original_dte=original_dte,
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
                    strategy_type="long_call_vertical",
                    action="close_spread",
                    legs=_shadow_legs,
                    net_limit_price=limit_price,
                    alpaca_order_id=close_order_id,
                )
            except Exception:
                logger.debug("shadow_execution recording failed (non-fatal)", exc_info=True)

        self.spread_tracker.mark_pending_close(spread_id, close_order_id)
        self.pending_order_id = close_order_id
        self.state = LongCallVerticalState.PENDING_CLOSE
        self._save_state()
        logger.info(
            "Long call vertical %s close submitted: %s (PENDING_CLOSE)",
            spread_id, close_order_id,
        )
        return True

