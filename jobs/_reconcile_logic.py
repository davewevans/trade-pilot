"""State reconstruction helpers for reconciliation jobs.

Pure functions — no IO, no broker calls. Takes an AccountSnapshot plus
local state and derives expected broker state or detects orphans. Designed
to be easy to unit-test in isolation.
"""

from __future__ import annotations

from jobs._broker_snapshot import AccountSnapshot


def derive_wheel_state_from_positions(symbol: str, positions: list[dict]) -> str:
    """Mirror of WheelStrategy.get_current_state logic, but pure.

    Returns one of "IDLE", "SHORT_PUT", "LONG_STOCK", "SHORT_CALL".
    Rules (checked in order):
        - Short put for symbol (OCC, qty < 0, type=P) → SHORT_PUT
        - Short call for symbol (OCC, qty < 0, type=C) → SHORT_CALL
        - Equity position for symbol with qty >= 100 → LONG_STOCK
        - Else → IDLE

    Args:
        symbol: Underlying root ticker (e.g. "AAPL").
        positions: List of normalised position dicts from AccountSnapshot.positions.
    """
    from utils.occ import parse_occ

    root = symbol.upper()
    equity_qty: float = 0.0

    for pos in positions:
        pos_symbol = (pos.get("symbol") or "").upper()
        qty = float(pos.get("qty") or 0)
        asset_class = (pos.get("asset_class") or "").lower()

        # Option position whose OCC root matches
        if asset_class in ("us_option", "option"):
            parsed = parse_occ(pos_symbol)
            if parsed and parsed["root"] == root:
                if parsed["option_type"] == "P" and qty < 0:
                    return "SHORT_PUT"
                if parsed["option_type"] == "C" and qty < 0:
                    return "SHORT_CALL"
        # Equity position
        elif asset_class in ("us_equity", "equity", "") and pos_symbol == root:
            equity_qty = qty

    # qty exactly 100 (or more) → LONG_STOCK; qty 99 → IDLE
    if equity_qty >= 100:
        return "LONG_STOCK"

    return "IDLE"


def reconstruct_spread_identities(
    account_positions: list[dict],
    tracker_open_spreads: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Match tracker spreads to broker legs.

    Returns (matched_spreads, untracked_legs).

    A spread is "matched" if every one of its leg symbols is present on
    the broker with the expected side (sell_to_open → negative qty;
    buy_to_open → positive qty). An untracked_leg is any broker option
    position that does not belong to any tracked spread AND is not a
    wheel position (equity or OCC option not in any tracker spread).

    Args:
        account_positions: Normalised position dicts from AccountSnapshot.positions.
        tracker_open_spreads: Active spread dicts from SpreadTracker.get_active_spreads().
    """
    from utils.occ import parse_occ

    # Index broker option positions by OCC symbol
    broker_option_by_symbol: dict[str, dict] = {}
    for pos in account_positions:
        sym = (pos.get("symbol") or "").upper()
        asset_class = (pos.get("asset_class") or "").lower()
        if asset_class in ("us_option", "option") or parse_occ(sym) is not None:
            broker_option_by_symbol[sym] = pos

    # Build the set of symbols that belong to at least one tracked spread
    all_tracker_leg_symbols: set[str] = set()
    for spread in tracker_open_spreads:
        for leg in spread.get("legs", []):
            sym = (leg.get("symbol") or "").upper()
            if sym:
                all_tracker_leg_symbols.add(sym)

    matched_spreads: list[dict] = []
    for spread in tracker_open_spreads:
        legs = spread.get("legs", [])
        spread_matched = True
        for leg in legs:
            sym = (leg.get("symbol") or "").upper()
            if not sym:
                spread_matched = False
                break
            broker_pos = broker_option_by_symbol.get(sym)
            if broker_pos is None:
                spread_matched = False
                break
            # Check side: sell_to_open → expect negative broker qty
            intent = str(leg.get("position_intent") or "").lower()
            broker_qty = float(broker_pos.get("qty") or 0)
            if "sell_to_open" in intent and broker_qty >= 0:
                spread_matched = False
                break
            if "buy_to_open" in intent and broker_qty <= 0:
                spread_matched = False
                break
        if spread_matched:
            matched_spreads.append(spread)

    # Untracked legs: broker option positions with OCC symbols not in any tracker spread
    untracked_legs: list[dict] = [
        pos for sym, pos in broker_option_by_symbol.items()
        if sym not in all_tracker_leg_symbols
    ]

    return matched_spreads, untracked_legs


def classify_untracked_positions(
    account_snapshot: AccountSnapshot,
    wheel_symbols: set[str],
    tracker_leg_symbols: set[str],
) -> list[dict]:
    """Return broker positions that are neither wheel-expected nor spread-tracked.

    These are suspected orphans requiring operator review.

    Args:
        account_snapshot: Broker snapshot for the account.
        wheel_symbols: Set of underlying tickers tracked by wheel/turnover_wheel
                       strategies with a non-IDLE state.
        tracker_leg_symbols: OCC symbols of all active spread legs.
    """
    from utils.occ import parse_occ

    orphans: list[dict] = []
    for pos in account_snapshot.positions:
        sym = (pos.get("symbol") or "").upper()
        asset_class = (pos.get("asset_class") or "").lower()
        parsed = parse_occ(sym)

        if parsed is not None:
            # Option position
            if sym in tracker_leg_symbols:
                continue  # spread-tracked
            if parsed["root"] in wheel_symbols:
                continue  # wheel-tracked
            orphans.append(pos)
        else:
            # Equity position — wheel LONG_STOCK tracks these
            if sym in wheel_symbols:
                continue
            # Small equity positions (qty < 100) are not wheel-expected
            # but are not necessarily orphans either; exclude them since
            # they could be fractional shares or dividend reinvestment.
            # Flag only positions >= 100 shares that we don't expect.
            if float(pos.get("qty") or 0) >= 100 and sym not in wheel_symbols:
                orphans.append(pos)

    return orphans
