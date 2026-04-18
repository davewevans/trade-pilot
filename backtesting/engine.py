"""Backtesting engine for options strategies using ORATS historical data.

Core loop per trading day:
  1. Fetch historical VIX + SPY SMA from yfinance (no API cost, cached locally).
  2. Fetch ORATS hist/summaries → IV rank, ATM IV, skew per symbol.
  3. Derive market regime using the same derive_market_regime() logic.
  4. Run StrategyRouter to determine which strategies would be active.
  5. For eligible strategies, fetch hist/strikes to find entry candidates.
  6. Simulate entry: record mid price as entry credit.
  7. Each subsequent day for open positions, look up the contract's current
     value and check management rules (50% profit, DTE ≤ 7, max loss 2×).
  8. On exit, compute P&L and append to trade log.

Usage::

    from backtesting.engine import BacktestEngine, BacktestParams
    params = BacktestParams(
        strategy="bull_put_spread",
        symbols=["SPY", "AAPL"],
        start_date="2023-01-01",
        end_date="2023-12-31",
        delta=0.30,
        dte_min=21,
        dte_max=45,
        ivr_threshold=30.0,
        profit_close_pct=0.50,
        contracts=1,
    )
    result = BacktestEngine().run(params, progress_cb=print)
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── Data structures ───────────────────────────────────────────────────────────

SUPPORTED_STRATEGIES = [
    "wheel_csp",
    "bull_put_spread",
    "bear_call_spread",
    "iron_condor",
    "long_call_vertical",
]

# ORATS slippage model: leg count → percent of bid-ask width traveled past mid
_STRATEGY_LEG_COUNTS: dict[str, int] = {
    "wheel_csp": 1,
    "bull_put_spread": 2,
    "bear_call_spread": 2,
    "long_call_vertical": 2,
    "iron_condor": 4,
}
_ORATS_SLIPPAGE_BY_LEGS: dict[int, float] = {1: 0.75, 2: 0.66, 4: 0.53}


@dataclass
class BacktestParams:
    strategy: str = "bull_put_spread"
    symbols: list[str] = field(default_factory=lambda: ["SPY"])
    start_date: str = "2023-01-01"
    end_date: str = "2023-12-31"
    delta: float = 0.30
    dte_min: int = 21
    dte_max: int = 45
    ivr_threshold: float = 30.0
    profit_close_pct: float = 0.50
    contracts: int = 1
    # Spread width in strike units (for bull/bear/condor)
    spread_width_strikes: int = 5
    # Slippage model: "orats" uses ORATS calibrated percentages by leg count,
    # "none" disables slippage, "custom" uses custom_slippage_pct for all legs.
    slippage_model: str = "orats"
    custom_slippage_pct: float = 0.0


@dataclass
class SimulatedTrade:
    symbol: str
    strategy: str
    entry_date: str
    exit_date: Optional[str]
    expiration_date: str
    short_strike: float
    long_strike: Optional[float]       # None for naked puts/calls
    short_strike_2: Optional[float]    # call side of iron condor
    long_strike_2: Optional[float]
    entry_credit: float                # per-share credit collected
    exit_debit: Optional[float]        # per-share cost to close
    contracts: int
    pnl: Optional[float]               # dollars (positive = profit)
    exit_reason: Optional[str]         # profit_target | dte_expired | max_loss | still_open
    entry_delta: float
    entry_ivr: float
    entry_regime: str
    entry_iv_env: str
    holding_days: Optional[int]
    # Earnings move tracking — populated when an earnings event fell
    # inside the holding period.
    entry_implied_earnings_move: Optional[float] = None   # what options priced at entry
    entry_historical_earnings_move: Optional[float] = None # stock's historical avg move
    earnings_iv_premium_at_entry: Optional[float] = None  # (implied-hist)/hist at entry
    actual_earnings_move: Optional[float] = None           # realised stock move on event day
    earnings_occurred_in_window: bool = False              # did earnings fall in hold period?


@dataclass
class BacktestResult:
    params: dict
    trades: list[SimulatedTrade]
    # Computed metrics
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_trade_pnl: float = 0.0
    max_drawdown: float = 0.0
    avg_duration_days: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    monthly_returns: list[dict] = field(default_factory=list)   # [{month, pnl}]
    equity_curve: list[dict] = field(default_factory=list)       # [{date, cumulative}]
    total_slippage_cost: float = 0.0                             # total cost of slippage across all trades


# ── Open position tracker ─────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    symbol: str
    strategy: str
    entry_date: str
    expiration_date: str
    short_strike: float
    long_strike: Optional[float]
    short_strike_2: Optional[float]
    long_strike_2: Optional[float]
    option_type: str                   # "put" for put side, "call" for call side
    option_type_2: Optional[str]       # for iron condor call side
    entry_credit: float                # net credit per share
    entry_delta: float
    entry_ivr: float
    entry_regime: str
    entry_iv_env: str
    entry_dte: int
    contracts: int
    # Earnings context captured at entry from ORATS /cores
    implied_earnings_move: Optional[float] = None
    historical_earnings_move: Optional[float] = None
    earnings_iv_premium: Optional[float] = None
    earnings_date: Optional[str] = None          # next earnings date at time of entry


# ── Slippage helpers ──────────────────────────────────────────────────────────

def _apply_slippage(
    mid_price: float,
    strategy: str,
    side: str,
    slippage_model: str = "orats",
    custom_slippage_pct: float = 0.0,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
) -> float:
    """Apply ORATS slippage model to a mid price and return the adjusted price.

    Args:
        mid_price: The theoretical mid price of the contract or spread.
        strategy: Strategy name — determines leg count and therefore slippage %.
        side: ``"entry"`` or ``"exit"``.  For credit strategies entry=sell and
            exit=buy; for ``long_call_vertical`` it is the reverse.
        slippage_model: ``"orats"`` | ``"none"`` | ``"custom"``.
        custom_slippage_pct: Used only when slippage_model == ``"custom"``.
        bid / ask: If provided, use the real spread; otherwise synthesise a 3%
            spread around mid (bid = mid × 0.985, ask = mid × 1.015).

    Returns:
        Adjusted price that is *worse* than mid for the trader (they receive
        less when selling, or pay more when buying).
    """
    if slippage_model == "none" or mid_price <= 0:
        return mid_price

    legs = _STRATEGY_LEG_COUNTS.get(strategy, 1)
    if slippage_model == "orats":
        slippage_pct = _ORATS_SLIPPAGE_BY_LEGS.get(legs, 0.75)
    else:  # "custom"
        slippage_pct = custom_slippage_pct

    if bid is None or ask is None:
        # Synthetic spread: 3% of mid (conservative but better than raw mid)
        bid = mid_price * 0.985
        ask = mid_price * 1.015

    spread = ask - bid

    # For credit strategies: entry=sell, exit=buy
    # For long_call_vertical: entry=buy, exit=sell
    is_debit_strategy = (strategy == "long_call_vertical")
    is_sell = (side == "entry") != is_debit_strategy  # XOR

    if is_sell:
        # Seller receives less than mid: ask - spread * pct
        return ask - spread * slippage_pct
    else:
        # Buyer pays more than mid: bid + spread * pct
        return bid + spread * slippage_pct


# ── Engine ────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """Runs a historical strategy simulation over ORATS data."""

    def __init__(self) -> None:
        from data.orats_historical import ORATSHistorical
        self._orats = ORATSHistorical()
        self._slippage_model: str = "orats"
        self._custom_slippage_pct: float = 0.0
        self._total_slippage_cost: float = 0.0

    def estimate_cost(
        self,
        symbols: list,
        strategy: str,
        start_date: str,
        end_date: str,
    ) -> int:
        """Estimate ORATS API calls for a backtest given current cache state.

        Uses a ~2-calls-per-trading-day model (hist/summaries + hist/strikes on
        entry-eligible days) and credits existing cache hits.

        Args:
            symbols:    List of ticker symbols.
            strategy:   Strategy name (not currently used — call pattern is the same
                        per-day regardless of strategy).
            start_date: ISO date "YYYY-MM-DD".
            end_date:   ISO date "YYYY-MM-DD".

        Returns:
            Estimated number of non-cached ORATS historical API calls.
        """
        from datetime import date as _date
        from research.backtesting.sweep import _count_hist_summaries_cached
        from data.orats_cache import ORATSCache

        d1 = _date.fromisoformat(start_date)
        d2 = _date.fromisoformat(end_date)
        calendar_days = max(1, (d2 - d1).days)
        # Approximate 252 trading days per year
        est_trading_days = max(1, int(calendar_days * 252 / 365))

        # Each cold day costs ~2 calls: 1 hist/summaries (every day) +
        # ~1 hist/strikes (on entry-eligible days, roughly half of trading days).
        calls_per_cold_day = 2

        cache = ORATSCache()
        total = 0
        for sym in symbols:
            fresh = _count_hist_summaries_cached(cache, sym.upper(), est_trading_days)
            cold_days = max(0, est_trading_days - fresh)
            total += cold_days * calls_per_cold_day

        return total

    def run(
        self,
        params: BacktestParams,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> BacktestResult:
        """Execute the backtest and return a result object."""

        def log(msg: str) -> None:
            logger.info(msg)
            if progress_cb:
                progress_cb(msg)

        self._slippage_model = params.slippage_model
        self._custom_slippage_pct = params.custom_slippage_pct
        self._total_slippage_cost = 0.0

        log(f"Backtest starting: {params.strategy} | {params.symbols} | "
            f"{params.start_date} → {params.end_date} | slippage={params.slippage_model}")

        # ── 1. Load market data (VIX, SPY SMAs) from yfinance ──────────────
        log("Fetching historical VIX + SPY price data from yfinance...")
        market_data = _load_market_data(params.start_date, params.end_date)
        trading_days = sorted(market_data.keys())

        log(f"Fetching per-symbol price history for earnings move computation...")
        self._symbol_prices = _load_symbol_prices(params.symbols, params.start_date, params.end_date)

        if not trading_days:
            log("ERROR: No trading days found in range.")
            return BacktestResult(params=asdict(params), trades=[])

        log(f"Found {len(trading_days)} trading days.")

        # ── 2. Main simulation loop ─────────────────────────────────────────
        completed_trades: list[SimulatedTrade] = []
        open_positions: list[OpenPosition] = []
        day_count = len(trading_days)

        for i, trade_date in enumerate(trading_days):
            pct = int((i + 1) / day_count * 100)
            if i % 10 == 0 or i == day_count - 1:
                log(f"Processing {trade_date} ({pct}%)...")

            day_info = market_data[trade_date]

            # ── 2a. Close / manage open positions ──────────────────────────
            still_open: list[OpenPosition] = []
            for pos in open_positions:
                trade = self._manage_position(pos, trade_date, day_info)
                if trade is not None:
                    completed_trades.append(trade)
                else:
                    still_open.append(pos)
            open_positions = still_open

            # ── 2b. Evaluate new entries (one per symbol per day) ──────────
            regime = day_info["regime"]
            iv_env = day_info["iv_env"]

            if not self._regime_allows_entry(params.strategy, regime, iv_env):
                continue

            for symbol in params.symbols:
                # Don't stack more than one position per symbol at a time
                if any(p.symbol == symbol for p in open_positions):
                    continue

                summary = self._orats.get_summary_on_date(symbol, trade_date)
                if summary is None:
                    continue

                ivr = summary.get("iv_rank_1y")
                if ivr is None or ivr < params.ivr_threshold:
                    continue

                pos = self._try_entry(params, symbol, trade_date, summary, regime, iv_env)
                if pos is not None:
                    open_positions.append(pos)
                    log(f"  ENTRY {params.strategy} {symbol} on {trade_date} "
                        f"(IVR={ivr:.0f}, regime={regime})")

        # ── 3. Force-close any remaining open positions at expiration ────────
        for pos in open_positions:
            trade = self._force_close(pos, trading_days)
            completed_trades.append(trade)

        # ── 4. Compute metrics ───────────────────────────────────────────────
        result = self._compute_metrics(params, completed_trades)
        result.total_slippage_cost = round(self._total_slippage_cost, 2)
        log(f"Backtest complete: {result.total_trades} trades, "
            f"P&L=${result.total_pnl:.0f}, win rate={result.win_rate:.1f}%, "
            f"slippage cost=${result.total_slippage_cost:.0f}")
        return result

    # ── Entry simulation ─────────────────────────────────────────────────

    def _get_earnings_context(
        self, symbol: str, trade_date: str,
    ) -> tuple[Optional[float], Optional[float], Optional[float], Optional[str]]:
        """Return (implied_move, hist_move, iv_premium, earnings_date) from ORATS cores.

        All values may be None if data is unavailable.  The ``earnings_date``
        is the *next* scheduled event from the perspective of ``trade_date``.
        """
        try:
            cores = self._orats.get_cores_on_date(symbol, trade_date)
        except Exception:
            logger.debug("get_cores_on_date failed for %s on %s", symbol, trade_date)
            return None, None, None, None

        if not cores:
            return None, None, None, None

        implied = cores.get("implied_earnings_move")
        hist = cores.get("abs_avg_earnings_move")

        premium: Optional[float] = None
        if implied is not None and hist and hist != 0:
            premium = round((implied - hist) / abs(hist), 4)

        # ORATS field naming varies across API versions; try common keys.
        ern_date = (
            cores.get("earn_date")
            or cores.get("earnings_date")
            or cores.get("next_earn_date")
            or cores.get("earnDate")
        )
        # Normalise to ISO string if it looks like a date
        if ern_date and not isinstance(ern_date, str):
            try:
                ern_date = str(ern_date)[:10]
            except Exception:
                ern_date = None
        elif isinstance(ern_date, str):
            ern_date = ern_date[:10] if len(ern_date) >= 10 else None

        return implied, hist, premium, ern_date

    def _try_entry(
        self,
        params: BacktestParams,
        symbol: str,
        trade_date: str,
        summary: dict,
        regime: str,
        iv_env: str,
    ) -> Optional[OpenPosition]:
        """Attempt to find and record an entry for the given strategy."""

        strategy = params.strategy

        # Fetch earnings context once; applied to whichever branch creates a position.
        ern_implied, ern_hist, ern_premium, ern_date = self._get_earnings_context(
            symbol, trade_date,
        )

        if strategy in ("wheel_csp", "bull_put_spread", "iron_condor"):
            put_contracts = self._orats.get_strikes_on_date(
                symbol, trade_date,
                dte_min=params.dte_min, dte_max=params.dte_max,
                delta_min=params.delta - 0.07, delta_max=params.delta + 0.07,
                option_type="put",
            )
            if not put_contracts:
                return None

            # Pick the strike closest to target delta
            short_put = _pick_closest_delta(put_contracts, -params.delta)
            if short_put is None or short_put["mid_price"] is None:
                return None
            if short_put["mid_price"] < 0.05:
                return None  # too cheap, skip

            long_strike: Optional[float] = None
            net_credit = short_put["mid_price"]

            if strategy == "bull_put_spread":
                # Buy a lower strike put for protection
                long_strike = round(short_put["strike"] - params.spread_width_strikes, 2)
                # Find the long leg's price from the same chain
                long_put = _find_by_strike(
                    self._orats.get_strikes_on_date(
                        symbol, trade_date,
                        dte_min=params.dte_min, dte_max=params.dte_max,
                        delta_min=0.01, delta_max=params.delta - 0.05,
                        option_type="put",
                    ),
                    long_strike, short_put["expiration_date"],
                )
                if long_put and long_put["mid_price"] is not None:
                    net_credit = round(short_put["mid_price"] - long_put["mid_price"], 4)
                else:
                    # Estimate long leg as 40% of short leg price
                    net_credit = round(short_put["mid_price"] * 0.60, 4)
                    long_strike = round(short_put["strike"] - params.spread_width_strikes, 2)

            if net_credit <= 0:
                return None

            raw_credit = net_credit
            net_credit = _apply_slippage(
                net_credit, strategy, "entry",
                self._slippage_model, self._custom_slippage_pct,
            )
            if net_credit <= 0:
                return None
            self._total_slippage_cost += (raw_credit - net_credit) * params.contracts * 100

            return OpenPosition(
                symbol=symbol,
                strategy=strategy,
                entry_date=trade_date,
                expiration_date=short_put["expiration_date"],
                short_strike=short_put["strike"],
                long_strike=long_strike,
                short_strike_2=None,
                long_strike_2=None,
                option_type="put",
                option_type_2=None,
                entry_credit=net_credit,
                entry_delta=abs(short_put["delta"] or params.delta),
                entry_ivr=summary.get("iv_rank_1y", 0),
                entry_regime=regime,
                entry_iv_env=iv_env,
                entry_dte=short_put["dte"] or params.dte_min,
                contracts=params.contracts,
                implied_earnings_move=ern_implied,
                historical_earnings_move=ern_hist,
                earnings_iv_premium=ern_premium,
                earnings_date=ern_date,
            )

        elif strategy == "bear_call_spread":
            call_contracts = self._orats.get_strikes_on_date(
                symbol, trade_date,
                dte_min=params.dte_min, dte_max=params.dte_max,
                delta_min=params.delta - 0.07, delta_max=params.delta + 0.07,
                option_type="call",
            )
            if not call_contracts:
                return None

            short_call = _pick_closest_delta(call_contracts, params.delta)
            if short_call is None or short_call["mid_price"] is None:
                return None
            if short_call["mid_price"] < 0.05:
                return None

            long_strike = round(short_call["strike"] + params.spread_width_strikes, 2)
            long_call = _find_by_strike(
                self._orats.get_strikes_on_date(
                    symbol, trade_date,
                    dte_min=params.dte_min, dte_max=params.dte_max,
                    delta_min=0.01, delta_max=params.delta - 0.05,
                    option_type="call",
                ),
                long_strike, short_call["expiration_date"],
            )
            if long_call and long_call["mid_price"] is not None:
                net_credit = round(short_call["mid_price"] - long_call["mid_price"], 4)
            else:
                net_credit = round(short_call["mid_price"] * 0.60, 4)

            if net_credit <= 0:
                return None

            raw_credit = net_credit
            net_credit = _apply_slippage(
                net_credit, strategy, "entry",
                self._slippage_model, self._custom_slippage_pct,
            )
            if net_credit <= 0:
                return None
            self._total_slippage_cost += (raw_credit - net_credit) * params.contracts * 100

            return OpenPosition(
                symbol=symbol,
                strategy=strategy,
                entry_date=trade_date,
                expiration_date=short_call["expiration_date"],
                short_strike=short_call["strike"],
                long_strike=long_strike,
                short_strike_2=None,
                long_strike_2=None,
                option_type="call",
                option_type_2=None,
                entry_credit=net_credit,
                entry_delta=abs(short_call["delta"] or params.delta),
                entry_ivr=summary.get("iv_rank_1y", 0),
                entry_regime=regime,
                entry_iv_env=iv_env,
                entry_dte=short_call["dte"] or params.dte_min,
                contracts=params.contracts,
                implied_earnings_move=ern_implied,
                historical_earnings_move=ern_hist,
                earnings_iv_premium=ern_premium,
                earnings_date=ern_date,
            )

        elif strategy == "long_call_vertical":
            # Debit spread: buy higher delta, sell lower delta call
            long_contracts = self._orats.get_strikes_on_date(
                symbol, trade_date,
                dte_min=params.dte_min, dte_max=params.dte_max,
                delta_min=0.45, delta_max=0.60,
                option_type="call",
            )
            if not long_contracts:
                return None

            long_call = _pick_closest_delta(long_contracts, 0.50)
            if long_call is None or long_call["mid_price"] is None:
                return None

            long_strike = round(long_call["strike"] + params.spread_width_strikes, 2)
            short_call = _find_by_strike(
                self._orats.get_strikes_on_date(
                    symbol, trade_date,
                    dte_min=params.dte_min, dte_max=params.dte_max,
                    delta_min=0.20, delta_max=0.40,
                    option_type="call",
                ),
                long_strike, long_call["expiration_date"],
            )

            if short_call and short_call["mid_price"] is not None:
                net_debit = round(long_call["mid_price"] - short_call["mid_price"], 4)
            else:
                net_debit = round(long_call["mid_price"] * 0.60, 4)

            if net_debit <= 0:
                return None

            # For debit strategy, entry is a BUY → slippage increases cost
            raw_debit = net_debit
            net_debit = _apply_slippage(
                net_debit, strategy, "entry",
                self._slippage_model, self._custom_slippage_pct,
            )
            self._total_slippage_cost += (net_debit - raw_debit) * params.contracts * 100

            # Store as negative credit (debit paid)
            return OpenPosition(
                symbol=symbol,
                strategy=strategy,
                entry_date=trade_date,
                expiration_date=long_call["expiration_date"],
                short_strike=long_call["strike"],           # long leg stored as "short_strike"
                long_strike=long_strike,                    # OTM short leg
                short_strike_2=None,
                long_strike_2=None,
                option_type="call",
                option_type_2=None,
                entry_credit=-net_debit,                    # negative = debit paid
                entry_delta=abs(long_call["delta"] or 0.50),
                entry_ivr=summary.get("iv_rank_1y", 0),
                entry_regime=regime,
                entry_iv_env=iv_env,
                entry_dte=long_call["dte"] or params.dte_min,
                contracts=params.contracts,
                implied_earnings_move=ern_implied,
                historical_earnings_move=ern_hist,
                earnings_iv_premium=ern_premium,
                earnings_date=ern_date,
            )

        return None

    # ── Position management ───────────────────────────────────────────────

    def _manage_position(
        self,
        pos: OpenPosition,
        trade_date: str,
        day_info: dict,
    ) -> Optional[SimulatedTrade]:
        """Check management rules. Return a closed SimulatedTrade, or None to keep open."""
        try:
            exp = date.fromisoformat(pos.expiration_date)
            trd = date.fromisoformat(trade_date)
        except ValueError:
            return None

        dte_remaining = (exp - trd).days

        # ── Rule: DTE ≤ 7 → close ────────────────────────────────────────
        if dte_remaining <= 7:
            exit_debit = self._lookup_contract_price(
                pos.symbol, trade_date, pos.short_strike,
                pos.expiration_date, pos.option_type,
            )
            # If we can't get current price, use 10% of original credit as estimate
            if exit_debit is None:
                exit_debit = abs(pos.entry_credit) * 0.10 if pos.entry_credit > 0 else abs(pos.entry_credit) * 1.50

            raw_exit = exit_debit
            exit_debit = _apply_slippage(
                exit_debit, pos.strategy, "exit",
                self._slippage_model, self._custom_slippage_pct,
            )
            if pos.strategy == "long_call_vertical":
                self._total_slippage_cost += (raw_exit - exit_debit) * pos.contracts * 100
            else:
                self._total_slippage_cost += (exit_debit - raw_exit) * pos.contracts * 100

            return self._build_trade(pos, trade_date, exit_debit, "dte_expired")

        # Only do full price lookup every 3 days to reduce API calls
        if (trd - date.fromisoformat(pos.entry_date)).days % 3 != 0:
            return None

        # ── Look up current option value ──────────────────────────────────
        current_short_price = self._lookup_contract_price(
            pos.symbol, trade_date, pos.short_strike,
            pos.expiration_date, pos.option_type,
        )
        if current_short_price is None:
            return None  # can't evaluate, keep open

        # For spreads, estimate long leg value as well
        if pos.long_strike is not None and pos.entry_credit > 0:
            current_long_price = self._lookup_contract_price(
                pos.symbol, trade_date, pos.long_strike,
                pos.expiration_date, pos.option_type,
            )
            if current_long_price is None:
                # Estimate: long decays proportionally
                ratio = current_short_price / (abs(pos.entry_credit) + 1e-6)
                long_entry_est = abs(pos.entry_credit) * 0.40
                current_long_price = long_entry_est * ratio
            exit_debit = round(current_short_price - current_long_price, 4)
        else:
            exit_debit = current_short_price

        if pos.strategy == "long_call_vertical":
            # Debit spread: hold for target gain, not 50% profit on credit
            # Max profit = spread_width - entry_debit; target at 100% gain on debit
            max_spread_value = (pos.long_strike or pos.short_strike + 5) - pos.short_strike
            entry_debit = abs(pos.entry_credit)
            current_spread_value = exit_debit
            gain_pct = (current_spread_value - entry_debit) / entry_debit if entry_debit else 0
            if gain_pct >= 1.0:  # 100% gain on debit
                raw_val = current_spread_value
                slipped_val = _apply_slippage(
                    raw_val, pos.strategy, "exit",
                    self._slippage_model, self._custom_slippage_pct,
                )
                self._total_slippage_cost += (raw_val - slipped_val) * pos.contracts * 100
                return self._build_trade(pos, trade_date, -slipped_val, "profit_target")
            if gain_pct <= -0.50:  # 50% loss stop
                raw_val = current_spread_value
                slipped_val = _apply_slippage(
                    raw_val, pos.strategy, "exit",
                    self._slippage_model, self._custom_slippage_pct,
                )
                self._total_slippage_cost += (raw_val - slipped_val) * pos.contracts * 100
                return self._build_trade(pos, trade_date, -slipped_val, "max_loss")
            return None

        # Apply exit slippage to the spread price (BUY to close credit spread)
        raw_exit = exit_debit
        exit_debit = _apply_slippage(
            exit_debit, pos.strategy, "exit",
            self._slippage_model, self._custom_slippage_pct,
        )
        self._total_slippage_cost += (exit_debit - raw_exit) * pos.contracts * 100

        # ── Rule: profit target (50% of credit) ──────────────────────────
        initial_credit = abs(pos.entry_credit)
        if exit_debit <= initial_credit * (1 - 0.50):
            return self._build_trade(pos, trade_date, exit_debit, "profit_target")

        # ── Rule: max loss (2× credit) ────────────────────────────────────
        if exit_debit >= initial_credit * 2.0:
            return self._build_trade(pos, trade_date, exit_debit, "max_loss")

        return None

    def _lookup_contract_price(
        self,
        symbol: str,
        trade_date: str,
        strike: float,
        expiration_date: str,
        option_type: str,
    ) -> Optional[float]:
        """Return the current mid price for a specific contract, or None."""
        contract = self._orats.find_contract_on_date(
            symbol, trade_date, strike, expiration_date, option_type,
        )
        if contract is None:
            return None
        return contract.get("mid_price")

    def _force_close(self, pos: OpenPosition, trading_days: list[str]) -> SimulatedTrade:
        """Close a position that's still open at end of backtest (at expiration value)."""
        # Find last available day at or before expiration
        last_day = trading_days[-1]
        try:
            exp = date.fromisoformat(pos.expiration_date)
            for d in reversed(trading_days):
                if date.fromisoformat(d) <= exp:
                    last_day = d
                    break
        except ValueError:
            pass

        # Try to get actual expiration value
        exit_debit = self._lookup_contract_price(
            pos.symbol, last_day, pos.short_strike,
            pos.expiration_date, pos.option_type,
        )
        if exit_debit is None:
            exit_debit = 0.0  # assume expires worthless
        else:
            raw_exit = exit_debit
            exit_debit = _apply_slippage(
                exit_debit, pos.strategy, "exit",
                self._slippage_model, self._custom_slippage_pct,
            )
            if pos.strategy == "long_call_vertical":
                self._total_slippage_cost += (raw_exit - exit_debit) * pos.contracts * 100
            else:
                self._total_slippage_cost += (exit_debit - raw_exit) * pos.contracts * 100

        return self._build_trade(pos, last_day, exit_debit, "still_open")

    def _build_trade(
        self,
        pos: OpenPosition,
        exit_date: str,
        exit_debit: float,
        exit_reason: str,
    ) -> SimulatedTrade:
        try:
            entry = date.fromisoformat(pos.entry_date)
            exit_ = date.fromisoformat(exit_date)
            holding = (exit_ - entry).days
        except ValueError:
            holding = None

        if pos.strategy == "long_call_vertical":
            # entry_credit is negative (debit paid); exit_debit is positive (spread value at close)
            pnl = round((exit_debit + pos.entry_credit) * pos.contracts * 100, 2)
        else:
            # credit spread: credit collected minus debit to close
            pnl = round((pos.entry_credit - exit_debit) * pos.contracts * 100, 2)

        # ── Earnings-in-window detection ──────────────────────────────────
        ern_occurred = False
        actual_ern_move: Optional[float] = None

        if pos.earnings_date:
            try:
                ern_dt = date.fromisoformat(pos.earnings_date)
                entry_dt = date.fromisoformat(pos.entry_date)
                exit_dt = date.fromisoformat(exit_date)

                if entry_dt <= ern_dt <= exit_dt:
                    ern_occurred = True
                    prices = getattr(self, "_symbol_prices", {}).get(pos.symbol, {})
                    ern_str = ern_dt.isoformat()
                    ern_close = prices.get(ern_str)

                    # Walk back up to 5 calendar days to find previous trading day
                    prev_close: Optional[float] = None
                    for back in range(1, 6):
                        check = (ern_dt - timedelta(days=back)).isoformat()
                        if check in prices:
                            prev_close = prices[check]
                            break

                    if prev_close and ern_close and prev_close != 0:
                        actual_ern_move = round(
                            (ern_close - prev_close) / prev_close, 4,
                        )
            except (ValueError, TypeError):
                pass

        return SimulatedTrade(
            symbol=pos.symbol,
            strategy=pos.strategy,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            expiration_date=pos.expiration_date,
            short_strike=pos.short_strike,
            long_strike=pos.long_strike,
            short_strike_2=pos.short_strike_2,
            long_strike_2=pos.long_strike_2,
            entry_credit=pos.entry_credit,
            exit_debit=exit_debit,
            contracts=pos.contracts,
            pnl=pnl,
            exit_reason=exit_reason,
            entry_delta=pos.entry_delta,
            entry_ivr=pos.entry_ivr,
            entry_regime=pos.entry_regime,
            entry_iv_env=pos.entry_iv_env,
            holding_days=holding,
            entry_implied_earnings_move=pos.implied_earnings_move,
            entry_historical_earnings_move=pos.historical_earnings_move,
            earnings_iv_premium_at_entry=pos.earnings_iv_premium,
            actual_earnings_move=actual_ern_move,
            earnings_occurred_in_window=ern_occurred,
        )

    # ── Routing logic ─────────────────────────────────────────────────────

    @staticmethod
    def _regime_allows_entry(strategy: str, regime: str, iv_env: str) -> bool:
        """Mirror the StrategyRouter routing rules for backtesting."""
        if regime == "CRASH":
            return False
        if strategy == "wheel_csp":
            return regime in ("NEUTRAL", "BULL") and iv_env in ("MODERATE", "HIGH")
        if strategy == "bull_put_spread":
            return regime in ("NEUTRAL", "BULL") and iv_env in ("MODERATE", "HIGH")
        if strategy == "bear_call_spread":
            return regime in ("BEAR", "NEUTRAL") and iv_env in ("MODERATE", "HIGH")
        if strategy == "iron_condor":
            return regime == "NEUTRAL" and iv_env == "HIGH"
        if strategy == "long_call_vertical":
            return regime == "BULL" and iv_env == "LOW"
        return False

    # ── Metrics ───────────────────────────────────────────────────────────

    @staticmethod
    def _compute_metrics(
        params: BacktestParams, trades: list[SimulatedTrade],
    ) -> BacktestResult:
        if not trades:
            return BacktestResult(params=asdict(params), trades=[])

        closed = [t for t in trades if t.pnl is not None]
        total_pnl = sum(t.pnl for t in closed)
        winners = [t for t in closed if t.pnl and t.pnl > 0]
        win_rate = len(winners) / len(closed) * 100 if closed else 0.0
        avg_pnl = total_pnl / len(closed) if closed else 0.0
        durations = [t.holding_days for t in closed if t.holding_days is not None]
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        # Max drawdown from cumulative P&L curve
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        equity_curve: list[dict] = []
        daily_pnl: dict[str, float] = {}
        for t in sorted(closed, key=lambda x: x.exit_date or ""):
            key = (t.exit_date or "")[:10]
            daily_pnl[key] = daily_pnl.get(key, 0.0) + (t.pnl or 0.0)
        for d in sorted(daily_pnl):
            cumulative += daily_pnl[d]
            equity_curve.append({"date": d, "cumulative_pnl": round(cumulative, 2)})
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        # Monthly returns
        monthly: dict[str, float] = {}
        for t in closed:
            month = (t.exit_date or "")[:7]  # YYYY-MM
            if month:
                monthly[month] = monthly.get(month, 0.0) + (t.pnl or 0.0)
        monthly_returns = [
            {"month": m, "pnl": round(v, 2)}
            for m, v in sorted(monthly.items())
        ]

        return BacktestResult(
            params=asdict(params),
            trades=closed,
            total_pnl=round(total_pnl, 2),
            win_rate=round(win_rate, 1),
            avg_trade_pnl=round(avg_pnl, 2),
            max_drawdown=round(max_dd, 2),
            avg_duration_days=round(avg_duration, 1),
            total_trades=len(closed),
            winning_trades=len(winners),
            monthly_returns=monthly_returns,
            equity_curve=equity_curve,
        )


# ── Market data helpers ───────────────────────────────────────────────────────

def _load_market_data(start_date: str, end_date: str) -> dict[str, dict]:
    """Load VIX + SPY from yfinance and compute daily regime info.

    Returns a dict keyed by ISO date string with:
      - vix: float
      - spy_close: float
      - above_sma_50: bool
      - above_sma_200: bool
      - regime: str
      - iv_env: str  (MODERATE as default — caller overrides with ORATS data)
    """
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        logger.error("yfinance is required for backtesting. pip install yfinance")
        return {}

    # Fetch extra history for SMA-200 warm-up
    extra = timedelta(days=300)
    fetch_start = (date.fromisoformat(start_date) - extra).isoformat()

    logger.info("Downloading ^VIX history...")
    vix_df = yf.download("^VIX", start=fetch_start, end=end_date, progress=False, auto_adjust=False)

    logger.info("Downloading SPY history...")
    spy_df = yf.download("SPY", start=fetch_start, end=end_date, progress=False, auto_adjust=True)

    if vix_df.empty or spy_df.empty:
        logger.error("yfinance returned empty data")
        return {}

    # Flatten multi-index columns if present
    if hasattr(vix_df.columns, "levels"):
        vix_df.columns = [c[0] for c in vix_df.columns]
    if hasattr(spy_df.columns, "levels"):
        spy_df.columns = [c[0] for c in spy_df.columns]

    spy_close = spy_df["Close"].squeeze()
    sma50 = spy_close.rolling(50).mean()
    sma200 = spy_close.rolling(200).mean()
    vix_close = vix_df["Close"].squeeze()

    from data.market_regime import _classify_regime

    result: dict[str, dict] = {}
    start_dt = date.fromisoformat(start_date)
    end_dt = date.fromisoformat(end_date)

    for idx in spy_close.index:
        try:
            d = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
        except Exception:
            continue
        if d < start_dt or d > end_dt:
            continue

        date_str = d.isoformat()
        vix = _safe_scalar(vix_close, idx)
        spy = _safe_scalar(spy_close, idx)
        s50 = _safe_scalar(sma50, idx)
        s200 = _safe_scalar(sma200, idx)

        above_50 = (spy is not None and s50 is not None and spy > s50)
        above_200 = (spy is not None and s200 is not None and spy > s200)
        below_50 = not above_50
        below_200 = not above_200

        regime = _classify_regime(vix, below_50, below_200, above_50, None)

        result[date_str] = {
            "vix": vix,
            "spy_close": spy,
            "above_sma_50": above_50,
            "above_sma_200": above_200,
            "regime": regime,
            "iv_env": "MODERATE",  # will be refined per-symbol using ORATS data
        }

    return result


def _load_symbol_prices(
    symbols: list[str], start_date: str, end_date: str,
) -> dict[str, dict[str, float]]:
    """Download per-symbol daily close prices from yfinance.

    Returns ``{symbol: {date_str: close_price}}``.  A small pre-buffer is
    fetched so we can look up the previous trading day's close when an
    earnings event falls on the first day of the window.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not available — per-symbol prices skipped")
        return {}

    extra = timedelta(days=10)
    fetch_start = (date.fromisoformat(start_date) - extra).isoformat()
    result: dict[str, dict[str, float]] = {}

    for symbol in symbols:
        try:
            df = yf.download(symbol, start=fetch_start, end=end_date, progress=False, auto_adjust=True)
            if df.empty:
                continue
            if hasattr(df.columns, "levels"):
                df.columns = [c[0] for c in df.columns]
            close = df["Close"].squeeze()
            prices: dict[str, float] = {}
            for idx, val in close.items():
                try:
                    d_str = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                    f = float(val) if not hasattr(val, "__len__") else float(val.iloc[0])
                    if not math.isnan(f):
                        prices[d_str] = round(f, 4)
                except Exception:
                    continue
            result[symbol] = prices
            logger.debug("Loaded %d price points for %s", len(prices), symbol)
        except Exception as exc:
            logger.warning("Failed to load price history for %s: %s", symbol, exc)

    return result


def _safe_scalar(series, idx) -> Optional[float]:
    try:
        val = series[idx]
        if hasattr(val, "__len__"):
            val = val.iloc[0]
        f = float(val)
        return None if math.isnan(f) else round(f, 4)
    except Exception:
        return None


# ── Strike selection helpers ──────────────────────────────────────────────────

def _pick_closest_delta(contracts: list[dict], target_delta: float) -> Optional[dict]:
    """Pick the contract with delta closest to target_delta."""
    best: Optional[dict] = None
    best_dist = float("inf")
    for c in contracts:
        d = c.get("delta")
        if d is None:
            continue
        dist = abs(float(d) - target_delta)
        if dist < best_dist:
            best_dist = dist
            best = c
    return best


def _find_by_strike(
    contracts: list[dict],
    target_strike: float,
    expiration_date: str,
) -> Optional[dict]:
    """Find a contract matching a specific strike and expiration."""
    for c in contracts:
        s = c.get("strike")
        e = c.get("expiration_date", "")
        if s is not None and abs(float(s) - target_strike) < 0.5 and e == expiration_date:
            return c
    # Fallback: just match strike (different expiration may be close enough)
    for c in contracts:
        s = c.get("strike")
        if s is not None and abs(float(s) - target_strike) < 1.0:
            return c
    return None
