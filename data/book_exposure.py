"""Cross-account book-level exposure aggregation and anti-crowding pre-check.

Data sources (all read-only, file-not-found is swallowed):
  - SNAPSHOTS_DIR/wheel_state.json         (WheelStrategy single-symbol state)
  - SNAPSHOTS_DIR/turnover_wheel_state.json (TurnoverWheelStrategy single-symbol state)
  - SpreadTracker().get_active_spreads()   (reads open_spreads.json)

Use compute_cross_account_book_exposure() to build the book view, and
check_anti_crowding() as the pre-check entry point from market_open.py.
"""

import logging
from datetime import datetime, timezone

from config import settings

logger = logging.getLogger(__name__)


def compute_cross_account_book_exposure() -> dict:
    """Walk all strategy state files and return a book-level exposure view.

    Returns:
        {
            "computed_at": "<ISO8601 UTC>",
            "by_underlying": {
                "AAPL": {
                    "positions": [
                        {
                            "strategy_type": "wheel",
                            "account_hint": "wheel",
                            "state": "SHORT_PUT",
                            "families": ["short_put"],
                            "source": "wheel_state.json",
                        },
                        ...
                    ],
                    "families": {
                        "short_put":       ["wheel", "bull_put_spread"],
                        "short_call":      [],
                        "long_directional": [],
                    },
                },
                ...
            },
            "sources": {
                "wheel_state":         "<mtime ISO>" or null,
                "turnover_wheel_state": "<mtime ISO>" or null,
                "open_spreads":        "<mtime ISO>" or null,
            },
        }
    """
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_underlying: dict[str, dict] = {}
    sources: dict[str, str | None] = {
        "wheel_state": None,
        "turnover_wheel_state": None,
        "open_spreads": None,
    }

    def _ensure_underlying(symbol: str) -> None:
        ul = symbol.upper()
        if ul not in by_underlying:
            by_underlying[ul] = {
                "positions": [],
                "families": {
                    "short_put": [],
                    "short_call": [],
                    "long_directional": [],
                },
            }

    def _add_position(symbol: str, strategy_type: str, account_hint: str, state: str,
                      families: list[str], source: str) -> None:
        _ensure_underlying(symbol)
        ul = symbol.upper()
        entry = by_underlying[ul]
        entry["positions"].append({
            "strategy_type": strategy_type,
            "account_hint": account_hint,
            "state": state,
            "families": families,
            "source": source,
        })
        for fam in families:
            if fam in entry["families"] and strategy_type not in entry["families"][fam]:
                entry["families"][fam].append(strategy_type)

    # ── Wheel state ─────────────────────────────────────────────────────────
    _wheel_path = settings.SNAPSHOTS_DIR / "wheel_state.json"
    if _wheel_path.exists():
        import json
        try:
            data = json.loads(_wheel_path.read_text(encoding="utf-8"))
            mtime = datetime.fromtimestamp(
                _wheel_path.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds")
            sources["wheel_state"] = mtime
            symbol = data.get("symbol")
            state = data.get("state", "IDLE")
            if symbol and state != "IDLE":
                if state == "SHORT_PUT":
                    families = ["short_put"]
                elif state == "SHORT_CALL":
                    families = ["short_call"]
                else:
                    # LONG_STOCK: long equity is not in any option family
                    families = []
                _add_position(symbol, "wheel", "wheel", state, families, "wheel_state.json")
        except Exception:
            logger.warning("Failed to read wheel_state.json", exc_info=True)

    # ── Turnover Wheel state ─────────────────────────────────────────────────
    _tw_path = settings.SNAPSHOTS_DIR / "turnover_wheel_state.json"
    if _tw_path.exists():
        import json
        try:
            data = json.loads(_tw_path.read_text(encoding="utf-8"))
            mtime = datetime.fromtimestamp(
                _tw_path.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds")
            sources["turnover_wheel_state"] = mtime
            symbol = data.get("symbol")
            state = data.get("state", "IDLE")
            if symbol and state != "IDLE":
                if state == "SHORT_PUT":
                    families = ["short_put"]
                elif state == "SHORT_CALL":
                    families = ["short_call"]
                else:
                    # LONG_STOCK: excluded from all option families
                    families = []
                _add_position(symbol, "turnover_wheel", "turnover_wheel", state, families,
                              "turnover_wheel_state.json")
        except Exception:
            logger.warning("Failed to read turnover_wheel_state.json", exc_info=True)

    # ── Spread tracker (open_spreads.json) ───────────────────────────────────
    try:
        from data.spread_tracker import SpreadTracker
        tracker = SpreadTracker()
        _spreads_path = settings.SNAPSHOTS_DIR / "open_spreads.json"
        if _spreads_path.exists():
            mtime = datetime.fromtimestamp(
                _spreads_path.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds")
            sources["open_spreads"] = mtime

        for spread in tracker.get_active_spreads():
            s_type = spread.get("strategy_type", "")
            underlying = spread.get("underlying", "")
            status = spread.get("status", "")
            if not s_type or not underlying:
                continue
            if s_type not in settings.DIRECTIONAL_FAMILY_MAP:
                logger.warning(
                    "book_exposure: unknown strategy_type %r in open_spreads — skipping "
                    "(add to DIRECTIONAL_FAMILY_MAP to include in anti-crowding)",
                    s_type,
                )
                continue
            fam_entry = settings.DIRECTIONAL_FAMILY_MAP[s_type]
            families = list(fam_entry.get("families", []))
            _add_position(underlying, s_type, s_type, status.upper(), families, "open_spreads.json")
    except Exception:
        logger.warning("Failed to read SpreadTracker state", exc_info=True)

    return {
        "computed_at": computed_at,
        "by_underlying": by_underlying,
        "sources": sources,
    }


def check_anti_crowding(
    underlying: str,
    strategy_type: str,
    book: dict | None = None,
) -> tuple[bool, str]:
    """Return (allowed, reason). If allowed is False, reason is human-readable.

    Raises:
        ValueError: if `strategy_type` is not a key in settings.DIRECTIONAL_FAMILY_MAP.
            This is deliberately strict — silently permitting unknown strategy types
            would make this check a no-op for anything not yet wired into the family
            mapping (e.g. future strategies, or adaptive_spreads leaking as strategy_type).

    Returns (True, "") when:
      - config.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED is False.
      - The entering strategy has no families (e.g. calendar_spread with families=[]).
      - No existing position on this underlying occupies any family the entering
        strategy would occupy.
      - The only existing positions in an overlapping family are peers listed in
        same_underlying_peers for that family.

    Returns (False, "<reason>") when an overlapping family is occupied by a
    non-peer strategy. The reason names the blocking strategy and family.
    Callers should record a skip with SkipReason.ANTI_CROWDING_CROSS_ACCOUNT
    and SkipGate.PORTFOLIO.
    """
    if strategy_type not in settings.DIRECTIONAL_FAMILY_MAP:
        raise ValueError(
            f"check_anti_crowding: unknown strategy_type {strategy_type!r}. "
            f"Valid types: {list(settings.DIRECTIONAL_FAMILY_MAP.keys())}. "
            "If this is a new strategy, add it to config.DIRECTIONAL_FAMILY_MAP first."
        )

    if not settings.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED:
        return True, ""

    fam_entry = settings.DIRECTIONAL_FAMILY_MAP[strategy_type]
    entering_families = fam_entry.get("families", [])
    if not entering_families:
        return True, ""

    if book is None:
        book = compute_cross_account_book_exposure()

    ul = underlying.upper()
    ul_data = book.get("by_underlying", {}).get(ul)
    if ul_data is None:
        return True, ""

    for family in entering_families:
        existing = ul_data.get("families", {}).get(family, [])
        peers = fam_entry.get("same_underlying_peers", {}).get(family, [])
        blocking = [s for s in existing if s != strategy_type and s not in peers]
        if blocking:
            blocker = blocking[0]
            return (
                False,
                f"{blocker} already open on {underlying} — {family} family",
            )

    return True, ""
