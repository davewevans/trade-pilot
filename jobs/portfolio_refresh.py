"""Portfolio refresh job -- runs every 5 minutes during market hours.

Keeps the dashboard data fresh between decision cycles by updating
portfolio, circuit breaker, and spread reconciliation snapshots.
"""

import logging
from dataclasses import asdict

logger = logging.getLogger(__name__)


def run() -> None:
    """Fetch current equity/positions and update snapshot files."""
    logger.debug("=== PORTFOLIO REFRESH ===")

    from brokers.broker_factory import get_broker
    from data.spread_tracker import SpreadTracker
    from data.state_writer import StateWriter
    from strategies.circuit_breaker import CircuitBreaker

    broker = get_broker()
    sw = StateWriter()

    # ── Market-open check ───────────────────────────────────
    clock = broker.get_clock()
    if not clock.get("is_open"):
        return

    account = broker.get_account()
    positions = broker.get_all_positions()  # equity + options

    # ── Enrich option positions with live Greeks (Alpaca snapshot) ──────────
    # portfolio_refresh runs every 5 min during market hours, so Greeks are
    # at most 5 min stale.  No ORATS calls here — Alpaca snapshots are cheap.
    greeks_fetched_at: str | None = None
    try:
        from datetime import datetime as _dt
        option_symbols = [
            p.get("symbol", "") for p in positions
            if str(p.get("asset_class", "")).lower() == "us_option"
            and p.get("symbol")
        ]
        if option_symbols:
            snapshots = broker.get_option_snapshots(option_symbols)
            for p in positions:
                sym = p.get("symbol", "")
                snap = snapshots.get(sym, {})
                if snap:
                    p["delta"] = snap.get("delta")
                    p["theta"] = snap.get("theta")
                    p["vega"]  = snap.get("vega")
                    p["gamma"] = snap.get("gamma")
            greeks_fetched_at = _dt.now().isoformat(timespec="seconds")
    except Exception as e:
        logger.warning("Failed to enrich positions with Greeks: %s", e)

    from brokers.broker_factory import make_broker
    from jobs.startup_snapshot import ACCOUNT_BROKER_MAP

    # ── Circuit breaker update ──────────────────────────────
    cb = CircuitBreaker()
    equity = CircuitBreaker.calculate_portfolio_equity(make_broker)
    if equity is None:
        logger.warning(
            "Skipping circuit breaker update in portfolio_refresh: portfolio equity aggregation failed."
        )
        cb_status = cb._status
    else:
        cb_status = cb.update(equity)

    try:
        sw.write_circuit_breaker_status(asdict(cb_status))
    except Exception as e:
        logger.warning("Failed to write circuit breaker snapshot: %s", e)

    # ── Spread reconciliation ───────────────────────────────
    open_spreads: list[dict] = []
    spread_legs: set[str] = set()
    try:
        tracker = SpreadTracker()
        closed_ids = tracker.reconcile_with_alpaca(positions)
        for sid in closed_ids:
            tracker.close_spread(sid)
        open_spreads = tracker.to_snapshot()
        spread_legs = tracker.get_all_leg_symbols()
    except Exception as e:
        logger.warning("Failed to reconcile spreads: %s", e)

    # ── MAE (max-adverse-excursion) update ──────────────────────
    try:
        from datetime import datetime as _mae_dt
        from data.trade_journal import TradeJournal
        _mae_now = _mae_dt.now().isoformat(timespec="seconds")

        _position_pl: dict[str, float] = {}
        for _p in positions:
            _sym = (_p.get("symbol") or "").upper()
            if _sym:
                try:
                    _position_pl[_sym] = float(_p.get("unrealized_pl") or 0)
                except (TypeError, ValueError):
                    pass

        # Spread MAE: sum leg unrealized_pl for each open spread
        for _spread in tracker.get_open_spreads():
            _spread_pl = sum(
                _position_pl.get((_leg.get("symbol") or "").upper(), 0.0)
                for _leg in _spread.get("legs", [])
            )
            tracker.update_mae(_spread["spread_id"], _spread_pl, _mae_now)

        # Journal MAE: one read/write for all open wheel entries
        _journal = TradeJournal()
        _sym_pl_map = {sym: (pl, _mae_now) for sym, pl in _position_pl.items()}
        _journal.bulk_update_mae(_sym_pl_map)
    except Exception as _mae_err:
        logger.warning("MAE update failed (non-fatal): %s", _mae_err)

    # ── Portfolio snapshot (includes spreads) ────────────────
    try:
        from config import settings
        sw.write_portfolio_snapshot(
            account, positions, {},
            open_spreads=open_spreads,
            wheel_symbols=list(settings.WATCHLIST),
            spread_leg_symbols=spread_legs,
            greeks_fetched_at=greeks_fetched_at,
        )
    except Exception as e:
        logger.warning("Failed to write portfolio snapshot: %s", e)

    # ── Per-account snapshots (powers individual account cards) ──
    for acct_name, strategy_key in ACCOUNT_BROKER_MAP.items():
        try:
            acct_broker = make_broker(strategy_key)
            acct_data = acct_broker.get_account()
            acct_positions = acct_broker.get_all_positions()
            # Enrich per-account option positions with Greeks
            _acct_gfa: str | None = None
            try:
                from datetime import datetime as _adt
                _opt_syms = [
                    p.get("symbol", "") for p in acct_positions
                    if str(p.get("asset_class", "")).lower() == "us_option"
                    and p.get("symbol")
                ]
                if _opt_syms:
                    _snaps = acct_broker.get_option_snapshots(_opt_syms)
                    for p in acct_positions:
                        _s = _snaps.get(p.get("symbol", ""), {})
                        if _s:
                            p["delta"] = _s.get("delta")
                            p["theta"] = _s.get("theta")
                            p["vega"]  = _s.get("vega")
                            p["gamma"] = _s.get("gamma")
                    _acct_gfa = _adt.now().isoformat(timespec="seconds")
            except Exception as _eg:
                logger.warning("Greek enrichment failed for %s: %s", acct_name, _eg)
            sw.write_account_snapshot(acct_name, acct_data, acct_positions,
                                      greeks_fetched_at=_acct_gfa)
        except Exception as e:
            logger.warning(
                "Failed to refresh account snapshot for %s: %s", acct_name, e
            )

    # ── Equity history (powers the equity curve chart) ──────────
    try:
        history = broker.get_portfolio_history(period="3M", timeframe="1D")
        if history:
            sw.write_equity_history(history)
    except Exception as e:
        logger.warning("Failed to fetch/write equity history: %s", e)

    logger.debug(
        "Portfolio refresh: equity=%s | CB=%s | positions=%d",
        f"${equity:.2f}" if equity is not None else "N/A (aggregation failed)",
        cb_status.status, len(positions),
    )
