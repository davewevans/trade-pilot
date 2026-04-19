"""Turnover Wheel strategy: CSP → assignment → CC → called away → repeat.

Higher-turnover variant: shorter CC duration (7–14 DTE), no delta cap on CC
entry (cost basis only), wider position count (10 concurrent), smaller size
per position (5% of buying power), and up to 3 rolls before closing.
"""

import json
import logging
import os
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path

from brokers.base import BaseBroker
from config import settings

logger = logging.getLogger(__name__)

# One-time rename migration: move conservative_wheel_state.json → turnover_wheel_state.json.
# Points at the SNAPSHOTS_DIR location (the authoritative post-v1.5 path).
# Safe to remove this constant after a successful first run on each environment.
_LEGACY_STATE_FILE = settings.SNAPSHOTS_DIR / "conservative_wheel_state.json"
STATE_FILE = str(settings.SNAPSHOTS_DIR / "turnover_wheel_state.json")

# OCC symbols encode type at a fixed position: C = call, P = put.
# Format: ROOT(6) + YYMMDD(6) + C/P(1) + strike*1000(8)
_OCC_TYPE_OFFSET = 6 + 6  # 12th character (0-indexed)


class TurnoverWheelState(str, Enum):
    """Possible states in the turnover wheel strategy lifecycle."""

    IDLE = "IDLE"              # No position — ready to sell a CSP
    SHORT_PUT = "SHORT_PUT"    # Cash-secured put is open
    LONG_STOCK = "LONG_STOCK"  # Put was assigned — holding 100 shares
    SHORT_CALL = "SHORT_CALL"  # Covered call is open against the shares


class TurnoverWheelStrategy:
    """Manages the turnover wheel strategy lifecycle for a single underlying symbol.

    The turnover wheel cycles through four states:
        IDLE → SHORT_PUT → (assignment) → LONG_STOCK → SHORT_CALL → (called away) → IDLE

    Turnover Wheel rules (vs standard wheel):
    - CC DTE: 7–14 days (shorter gamma cycle for faster share turnover)
    - CC strike: above net cost basis only — no delta cap, no Bollinger Band constraint
    - Max position size: 5% of buying power (half of standard wheel)
    - Max concurrent positions: 10 (doubled vs standard wheel)
    - Max rolls: 3 before closing (vs 2 in standard wheel)
    - Roll delta constraint: wider (-0.40 for puts, 0.45 for calls)

    State is persisted to turnover_wheel_state.json so the bot can
    resume after restart. Actual broker positions always take precedence
    over the saved state.
    """

    # Maps entry states to research layer strategy types.
    # Only IDLE (CSP entry) and LONG_STOCK (CC entry) are gated.
    # Management states (SHORT_PUT, SHORT_CALL) have no mapping — they
    # are never liquidity-gated once a position is open.
    STRATEGY_TYPE_MAP: dict = {
        TurnoverWheelState.IDLE: "turnover_wheel_csp",
        TurnoverWheelState.LONG_STOCK: "turnover_wheel_cc",
    }

    def __init__(self, broker: BaseBroker, liquidity_repo=None, backtest_stats_repo=None):
        """Initialise the strategy with a broker and optional data client.

        Args:
            broker: A concrete BaseBroker implementation (e.g. AlpacaBroker).
            liquidity_repo: Optional LiquidityRepository. When provided,
                CSP and CC entries are gated by the liquidity tier. Defaults
                to None (no gating — existing behaviour preserved).
            backtest_stats_repo: Optional BacktestStatsRepository. When provided,
                CSP and CC entries are also gated by historical win-rate.
                Defaults to None (no gating — existing behaviour preserved).
        """
        self.broker = broker
        self._liquidity_repo = liquidity_repo
        self._backtest_stats_repo = backtest_stats_repo
        self.symbol: str | None = None
        self.state: TurnoverWheelState = TurnoverWheelState.IDLE
        self.open_position: dict | None = None
        # Effective cost basis tracking — see save_state/_load_state.
        # cost_basis = assignment strike − total premium collected this cycle
        self.cost_basis: float | None = None
        self.total_premium_collected: float = 0.0
        self.roll_count: int = 0
        # Price of the underlying at the time of initial CSP fill (not rolls).
        # Used to track entry price context for cycle analysis.
        # Set once on first SHORT_PUT detection; never overwritten on rolls.
        # Cleared when the cycle completes (IDLE reset).
        self.underlying_price_at_entry: float | None = None

        self._load_state()

    # ── state persistence ────────────────────────────────────

    def _load_state(self) -> None:
        """Load persisted state from turnover_wheel_state.json if it exists."""
        # One-time migration: rename conservative_wheel_state.json → turnover_wheel_state.json.
        # Safe to remove after a successful first run on each environment.
        legacy_path = _LEGACY_STATE_FILE
        new_path = Path(STATE_FILE)
        if legacy_path.exists() and not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_path), str(new_path))
            logger.info("Migrated conservative_wheel_state.json → turnover_wheel_state.json")

        if not os.path.exists(STATE_FILE):
            logger.info("No turnover wheel state file found at %s — starting fresh", STATE_FILE)
            return

        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            self.symbol = data.get("symbol")
            self.state = TurnoverWheelState(data.get("state", "IDLE"))
            self.open_position = data.get("open_position")
            self.cost_basis = data.get("cost_basis")
            self.total_premium_collected = float(
                data.get("total_premium_collected", 0.0) or 0.0
            )
            self.roll_count = int(data.get("roll_count", 0) or 0)
            # backward compat: missing field → None, no crash
            upae = data.get("underlying_price_at_entry")
            self.underlying_price_at_entry = float(upae) if upae is not None else None
            logger.info(
                "Loaded turnover wheel state: symbol=%s state=%s", self.symbol, self.state.value
            )
        except (json.JSONDecodeError, ValueError):
            logger.exception("Corrupt turnover wheel state file — resetting to IDLE")
            self.state = TurnoverWheelState.IDLE

    def save_state(self) -> None:
        """Persist current state to turnover_wheel_state.json."""
        data = {
            "symbol": self.symbol,
            "state": self.state.value,
            "open_position": self.open_position,
            "cost_basis": self.cost_basis,
            "total_premium_collected": self.total_premium_collected,
            "roll_count": self.roll_count,
            "underlying_price_at_entry": self.underlying_price_at_entry,
            "updated_at": datetime.now().isoformat(),
        }
        from utils.fileio import atomic_json_write
        atomic_json_write(Path(STATE_FILE), data)
        logger.info("Turnover wheel state saved: symbol=%s state=%s", self.symbol, self.state.value)

    # ── state reconciliation ─────────────────────────────────

    def get_current_state(self, symbol: str) -> TurnoverWheelState:
        """Determine the real turnover wheel state by inspecting broker positions and orders.

        Actual positions always win over the persisted state file.

        Logic:
            1. If there are open option orders for this symbol → check type
            2. If there is an open put position → SHORT_PUT
            3. If there is an open call position → SHORT_CALL
            4. If there is an equity/stock position of ≥100 shares → LONG_STOCK
            5. Otherwise → IDLE

        Args:
            symbol: The underlying ticker (e.g. "SPY").

        Returns:
            The reconciled TurnoverWheelState.
        """
        self.symbol = symbol
        root = symbol.upper()

        # Check option positions
        option_positions = self.broker.get_positions()
        for pos in option_positions:
            pos_symbol = (pos.get("symbol") or "").upper()
            if not pos_symbol.startswith(root):
                continue

            qty = float(pos.get("qty") or 0)
            occ_type = self._occ_option_type(pos_symbol)

            if occ_type == "P" and qty < 0:
                self.state = TurnoverWheelState.SHORT_PUT
                self.open_position = pos
                # Capture underlying price at first CSP detection (not on rolls).
                # roll_count > 0 means we've already been in SHORT_PUT before;
                # underlying_price_at_entry being None means this is a fresh entry.
                if self.underlying_price_at_entry is None:
                    try:
                        from data import market_data as _md
                        _tech = _md.get_stock_technicals(root)
                        _uprice = _tech.get("current_price") if _tech else None
                        if _uprice is not None:
                            self.underlying_price_at_entry = float(_uprice)
                        else:
                            # Fall back to strike price from the OCC symbol
                            from utils.occ import extract_strike
                            _strike = extract_strike(pos_symbol)
                            if _strike is not None:
                                self.underlying_price_at_entry = float(_strike)
                                logger.warning(
                                    "Turnover wheel: could not fetch current_price for %s — "
                                    "using strike %.2f as underlying_price_at_entry",
                                    root, _strike,
                                )
                    except Exception:
                        logger.warning(
                            "Turnover wheel: failed to capture underlying_price_at_entry for %s",
                            root, exc_info=True,
                        )
                self.save_state()
                logger.info("Turnover wheel reconciled state → SHORT_PUT (short put position found)")
                return self.state

            if occ_type == "C" and qty < 0:
                self.state = TurnoverWheelState.SHORT_CALL
                self.open_position = pos
                self.save_state()
                logger.info("Turnover wheel reconciled state → SHORT_CALL (short call position found)")
                return self.state

        # Check for stock position (assignment would create equity holding)
        try:
            stock_pos = self.broker.get_position(root)
            qty = float(stock_pos.get("qty") or 0)
            if qty >= 100:
                self.state = TurnoverWheelState.LONG_STOCK
                self.open_position = stock_pos
                # Recompute effective cost basis the first time we observe
                # the assignment (i.e. when cost_basis is unset). On
                # subsequent ticks we leave it untouched so manual edits
                # or future roll-credit adjustments survive.
                if self.cost_basis is None:
                    assignment_price = float(
                        stock_pos.get("avg_entry_price", 0) or 0
                    )
                    self.cost_basis = round(
                        assignment_price - self.total_premium_collected, 4,
                    )
                    logger.info(
                        "Turnover wheel reconciled state → LONG_STOCK (%s shares, "
                        "cost_basis=%.2f, assignment=%.2f, "
                        "premium_collected=%.2f)",
                        qty, self.cost_basis, assignment_price,
                        self.total_premium_collected,
                    )
                else:
                    logger.info("Turnover wheel reconciled state → LONG_STOCK (%s shares)", qty)
                self.save_state()
                return self.state
        except Exception:
            pass  # No stock position — that's fine

        # No relevant positions found — turnover wheel cycle complete; reset
        # cost-basis tracking so the next CSP starts from a clean slate.
        self.state = TurnoverWheelState.IDLE
        self.open_position = None
        self.cost_basis = None
        self.total_premium_collected = 0.0
        self.roll_count = 0
        self.underlying_price_at_entry = None
        self.save_state()
        logger.info("Turnover wheel reconciled state → IDLE (no positions for %s)", symbol)
        return self.state

    # ── liquidity gating ─────────────────────────────────────

    def evaluate_entry_liquidity(
        self,
        symbol: str,
        context: dict,
        state: TurnoverWheelState,
    ) -> dict | None:
        """Check the liquidity tier before a new CSP or CC entry.

        Entry states (IDLE → CSP, LONG_STOCK → CC) are gated; management
        states (SHORT_PUT, SHORT_CALL) are never touched.

        Returns:
            None — proceed with the normal decision flow. Liquidity metadata
            is attached to ``context["_research"]["liquidity"]`` so Claude
            and the recorder can see it.

            dict — a ready-made SKIP decision. The caller should short-circuit
            ``advisor.ask()`` and use this dict as the decision directly.
            Only returned when the symbol is Tier D (hard floor) and the kill
            switch is on.
        """
        strategy_type = self.STRATEGY_TYPE_MAP.get(state)
        if strategy_type is None:
            # Management state — no liquidity gating
            return None

        if self._liquidity_repo is not None:
            try:
                multiplier, tier, confidence = self._liquidity_repo.get_multiplier(
                    symbol, strategy_type,
                )
            except Exception:
                logger.warning(
                    "Liquidity multiplier lookup failed for %s/%s",
                    symbol, strategy_type, exc_info=True,
                )
                multiplier, tier, confidence = (1.0, "B", "none")
        else:
            multiplier, tier, confidence = (1.0, "B", "disabled")

        # Tier D = hard floor — reject before calling Claude
        if multiplier == 0.0:
            return {
                "action": "SKIP",
                "reasoning": f"Liquidity tier D for {strategy_type} — below floor",
                "skip_reason": "below_liquidity_floor",
                "_research": {
                    "liquidity": {
                        "multiplier": 0.0,
                        "tier": "D",
                        "confidence": confidence,
                        "final_score": 0.0,
                    }
                },
            }

        # Attach liquidity metadata to context for downstream logging.
        liq_multiplier = multiplier
        context.setdefault("_research", {})["liquidity"] = {
            "multiplier": liq_multiplier,
            "tier": tier,
            "confidence": confidence,
        }

        # ── Win-rate multiplier ───────────────────────────────────────────────
        if self._backtest_stats_repo is not None:
            try:
                wr_mult, wr_tier, wr_conf = self._backtest_stats_repo.get_winrate_multiplier(
                    symbol, strategy_type,
                )
            except Exception:
                logger.warning(
                    "Win-rate multiplier lookup failed for %s/%s",
                    symbol, strategy_type, exc_info=True,
                )
                wr_mult, wr_tier, wr_conf = (1.0, "neutral", "none")
        else:
            wr_mult, wr_tier, wr_conf = (1.0, "neutral", "disabled")

        if wr_mult == 0.0:
            return {
                "action": "SKIP",
                "reasoning": f"Win-rate below 30% floor for {strategy_type} — rejecting entry",
                "skip_reason": "below_winrate_floor",
                "_research": {
                    "liquidity": {"multiplier": liq_multiplier, "tier": tier, "confidence": confidence},
                    "winrate": {"multiplier": 0.0, "tier": wr_tier, "confidence": wr_conf},
                    "combined_multiplier": 0.0,
                },
            }

        combined_multiplier = liq_multiplier * wr_mult
        context["_research"]["winrate"] = {
            "multiplier": wr_mult,
            "tier": wr_tier,
            "confidence": wr_conf,
        }
        context["_research"]["combined_multiplier"] = combined_multiplier

        return None

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _occ_option_type(occ_symbol: str) -> str | None:
        """Extract 'C' or 'P' from an OCC option symbol."""
        from utils.occ import extract_option_type
        return extract_option_type(occ_symbol)
