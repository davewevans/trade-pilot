"""Wheel strategy implementation: CSP → assignment → CC → called away → repeat."""

import json
import logging
import os
import shutil
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path

from brokers.base import BaseBroker
from config import settings
from data import market_data

logger = logging.getLogger(__name__)

# Old source-relative path — used only during one-time migration on first load.
_LEGACY_STATE_FILE = Path(__file__).parent / ".." / "data" / "wheel_state.json"
STATE_FILE = str(settings.SNAPSHOTS_DIR / "wheel_state.json")

# OCC symbols encode type at a fixed position: C = call, P = put.
# Format: ROOT(6) + YYMMDD(6) + C/P(1) + strike*1000(8)
_OCC_TYPE_OFFSET = 6 + 6  # 12th character (0-indexed)


class WheelState(str, Enum):
    """Possible states in the wheel strategy lifecycle."""

    IDLE = "IDLE"              # No position — ready to sell a CSP
    SHORT_PUT = "SHORT_PUT"    # Cash-secured put is open
    LONG_STOCK = "LONG_STOCK"  # Put was assigned — holding 100 shares
    SHORT_CALL = "SHORT_CALL"  # Covered call is open against the shares


class WheelStrategy:
    """Manages the wheel strategy lifecycle for a single underlying symbol.

    The wheel cycles through four states:
        IDLE → SHORT_PUT → (assignment) → LONG_STOCK → SHORT_CALL → (called away) → IDLE

    State is persisted to a local JSON file so the bot can resume after restart.
    Actual broker positions always take precedence over the saved state.
    """

    # Maps entry states to research layer strategy types.
    # Only IDLE (CSP entry) and LONG_STOCK (CC entry) are gated.
    # Management states (SHORT_PUT, SHORT_CALL) have no mapping — they
    # are never liquidity-gated once a position is open.
    STRATEGY_TYPE_MAP: dict = {
        WheelState.IDLE: "wheel_csp",
        WheelState.LONG_STOCK: "wheel_cc",
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
        self.state: WheelState = WheelState.IDLE
        self.open_position: dict | None = None
        # Effective cost basis tracking — see save_state/_load_state.
        # cost_basis = assignment strike − total premium collected this cycle
        self.cost_basis: float | None = None
        self.total_premium_collected: float = 0.0
        self.roll_count: int = 0

        self._load_state()

    # ── state persistence ────────────────────────────────────

    def _load_state(self) -> None:
        """Load persisted state from wheel_state.json if it exists."""
        # One-time migration: move state from old source-relative path to persistent SNAPSHOTS_DIR.
        old_path = _LEGACY_STATE_FILE.resolve()
        new_path = Path(STATE_FILE)
        if old_path.exists() and not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_path, new_path)
            old_path.unlink()
            logger.info("Migrated wheel state from %s to %s", old_path, new_path)

        if not os.path.exists(STATE_FILE):
            logger.info("No state file found at %s — starting fresh", STATE_FILE)
            return

        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            self.symbol = data.get("symbol")
            self.state = WheelState(data.get("state", "IDLE"))
            self.open_position = data.get("open_position")
            self.cost_basis = data.get("cost_basis")
            self.total_premium_collected = float(
                data.get("total_premium_collected", 0.0) or 0.0
            )
            self.roll_count = int(data.get("roll_count", 0) or 0)
            logger.info(
                "Loaded state: symbol=%s state=%s", self.symbol, self.state.value
            )
        except (json.JSONDecodeError, ValueError):
            logger.exception("Corrupt state file — resetting to IDLE")
            self.state = WheelState.IDLE

    def save_state(self) -> None:
        """Persist current state to wheel_state.json."""
        data = {
            "symbol": self.symbol,
            "state": self.state.value,
            "open_position": self.open_position,
            "cost_basis": self.cost_basis,
            "total_premium_collected": self.total_premium_collected,
            "roll_count": self.roll_count,
            "updated_at": datetime.now().isoformat(),
        }
        from utils.fileio import atomic_json_write
        atomic_json_write(Path(STATE_FILE), data)
        logger.info("State saved: symbol=%s state=%s", self.symbol, self.state.value)

    @classmethod
    def read_persisted_state(cls, symbol: str) -> dict | None:
        """Return the raw persisted state dict without touching the broker.

        Used by reconciliation jobs to read local state for comparison against
        broker truth. Returns None if no state file exists or the stored symbol
        does not match. Never modifies state.
        """
        if not os.path.exists(STATE_FILE):
            return None
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            if data.get("symbol") == symbol:
                return data
            return None
        except Exception:
            logger.exception("WheelStrategy.read_persisted_state: failed to read %s", STATE_FILE)
            return None

    # ── state reconciliation ─────────────────────────────────

    def get_current_state(self, symbol: str) -> WheelState:
        """Determine the real wheel state by inspecting broker positions and orders.

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
            The reconciled WheelState.
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
                self.state = WheelState.SHORT_PUT
                self.open_position = pos
                self.save_state()
                logger.info("Reconciled state → SHORT_PUT (short put position found)")
                return self.state

            if occ_type == "C" and qty < 0:
                self.state = WheelState.SHORT_CALL
                self.open_position = pos
                self.save_state()
                logger.info("Reconciled state → SHORT_CALL (short call position found)")
                return self.state

        # Check for stock position (assignment would create equity holding)
        try:
            stock_pos = self.broker.get_position(root)
            qty = float(stock_pos.get("qty") or 0)
            if qty >= 100:
                self.state = WheelState.LONG_STOCK
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
                        "Reconciled state → LONG_STOCK (%s shares, "
                        "cost_basis=%.2f, assignment=%.2f, "
                        "premium_collected=%.2f)",
                        qty, self.cost_basis, assignment_price,
                        self.total_premium_collected,
                    )
                else:
                    logger.info("Reconciled state → LONG_STOCK (%s shares)", qty)
                self.save_state()
                return self.state
        except Exception:
            pass  # No stock position — that's fine

        # No relevant positions found — wheel cycle complete; reset
        # cost-basis tracking so the next CSP starts from a clean slate.
        self.state = WheelState.IDLE
        self.open_position = None
        self.cost_basis = None
        self.total_premium_collected = 0.0
        self.roll_count = 0
        self.save_state()
        logger.info("Reconciled state → IDLE (no positions for %s)", symbol)
        return self.state

    # ── liquidity gating ─────────────────────────────────────

    def evaluate_entry_liquidity(
        self,
        symbol: str,
        context: dict,
        state: WheelState,
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

    # ── context building ─────────────────────────────────────

    def build_context(self, symbol: str) -> dict:
        """Assemble the full context dict for Claude to make a decision.

        Gathers account info, current positions, technicals, the relevant
        option chain slice, earnings date, and IV rank. Each section is
        clearly labelled so it can be used as a structured prompt body.

        Args:
            symbol: The underlying ticker (e.g. "SPY").

        Returns:
            Dict with sections: account, wheel_state, positions, technicals,
            option_chain_puts, option_chain_calls, earnings, iv_rank.
        """
        self.symbol = symbol
        today = date.today()
        min_dte = today + timedelta(days=14)
        max_dte = today + timedelta(days=35)

        # ── account ──────────────────────────────────────────
        account = self.broker.get_account()

        # ── current state ────────────────────────────────────
        current_state = self.get_current_state(symbol)

        # ── open positions & orders ──────────────────────────
        positions = self.broker.get_positions()
        open_orders = self.broker.get_orders(status="open")

        # ── technicals ───────────────────────────────────────
        try:
            technicals = market_data.get_stock_technicals(symbol)
        except Exception:
            logger.exception("Failed to fetch technicals for %s", symbol)
            technicals = {}

        current_price = technicals.get("current_price", 0)

        # ── option chain: filter to 14-35 DTE, ±5% strike ───
        strike_low = current_price * 0.95
        strike_high = current_price * 1.05

        put_chain = self._filter_chain(
            symbol, "put", min_dte, max_dte, strike_low, strike_high
        )
        call_chain = self._filter_chain(
            symbol, "call", min_dte, max_dte, strike_low, strike_high
        )

        # ── earnings ─────────────────────────────────────────
        earnings_date = market_data.get_earnings_date(symbol)

        # ── IV rank ──────────────────────────────────────────
        try:
            iv_rank = market_data.get_iv_rank(symbol)
        except Exception:
            logger.exception("Failed to compute IV rank for %s", symbol)
            iv_rank = None

        return {
            "account": {
                "buying_power": account.get("buying_power"),
                "cash": account.get("cash"),
                "options_trading_level": account.get("options_trading_level"),
            },
            "wheel_state": {
                "symbol": symbol,
                "state": current_state.value,
                "open_position": self.open_position,
            },
            "positions": positions,
            "open_orders": open_orders,
            "technicals": technicals,
            "option_chain_puts": put_chain,
            "option_chain_calls": call_chain,
            "earnings": {
                "next_earnings_date": earnings_date,
                "days_until_earnings": (
                    (date.fromisoformat(earnings_date) - today).days
                    if earnings_date
                    else None
                ),
            },
            "iv_rank": iv_rank,
            "wheel_cost_basis": (
                {
                    "effective_cost_basis": self.cost_basis,
                    "assignment_price": float(
                        (self.open_position or {}).get("avg_entry_price", 0) or 0
                    ),
                    "total_premium_collected": self.total_premium_collected,
                    "roll_count": self.roll_count,
                }
                if current_state in (WheelState.LONG_STOCK, WheelState.SHORT_CALL)
                else None
            ),
            "metadata": {
                "symbol": symbol,
                "timestamp": datetime.now().isoformat(),
                "dte_range": f"{min_dte} to {max_dte}",
                "strike_range": f"{strike_low:.2f} to {strike_high:.2f}",
            },
        }

    # ── helpers ──────────────────────────────────────────────

    def _filter_chain(
        self,
        symbol: str,
        option_type: str,
        min_exp: date,
        max_exp: date,
        strike_low: float,
        strike_high: float,
    ) -> list[dict]:
        """Fetch option contracts and enrich with snapshot data.

        Args:
            symbol: Underlying ticker.
            option_type: "call" or "put".
            min_exp: Earliest acceptable expiration date.
            max_exp: Latest acceptable expiration date.
            strike_low: Minimum strike price.
            strike_high: Maximum strike price.

        Returns:
            List of contract dicts with snapshot data merged in.
        """
        contracts = self.broker.get_option_contracts(
            underlying_symbol=symbol, option_type=option_type
        )

        # Filter to DTE window and strike range
        filtered = []
        for c in contracts:
            exp = c.get("expiration_date", "")
            try:
                exp_date = date.fromisoformat(exp)
            except ValueError:
                continue

            strike = c.get("strike_price", 0)
            if min_exp <= exp_date <= max_exp and strike_low <= strike <= strike_high:
                filtered.append(c)

        if not filtered:
            return []

        # Enrich with snapshot data (bid/ask, greeks, IV)
        occ_symbols = [c["symbol"] for c in filtered]
        try:
            snapshots = market_data.get_option_snapshot(occ_symbols)
        except Exception:
            logger.exception("Failed to fetch snapshots for %s chain", option_type)
            return filtered  # return contracts without snapshot data

        for c in filtered:
            snap = snapshots.get(c["symbol"], {})
            c["snapshot"] = snap

        return filtered

    @staticmethod
    def _occ_option_type(occ_symbol: str) -> str | None:
        """Extract 'C' or 'P' from an OCC option symbol."""
        from utils.occ import extract_option_type
        return extract_option_type(occ_symbol)
