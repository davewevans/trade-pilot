"""Deterministic force-close rules for catastrophic positions.

These rules bypass Claude entirely and execute a close directly. They fire
only on positions that are both deeply in the money and near expiry — the
intersection where probabilistic reasoning is the wrong tool and a mechanical
rule should take over.

Kill switch: FORCE_CLOSE_ENABLED env var (default true).
"""

from dataclasses import dataclass
from typing import Optional
import logging

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class ForceCloseResult:
    rule_code: str
    reason: str
    suggested_limit_price: Optional[float]  # None if no quote available


# ── Thresholds (central so they're easy to tune) ────────────
DEEP_ITM_ABS_DELTA = 0.70
NEAR_EXPIRY_DTE = 3


def _is_enabled() -> bool:
    return getattr(settings, "FORCE_CLOSE_ENABLED", True)


def _compute_close_limit_buy_to_close(
    bid: float, ask: float,
) -> Optional[float]:
    """Compute an aggressive but bounded limit price for buying back a short option.

    Aggressive = mid + 40% of spread (willing to pay more than mid to get filled).
    Bounded = never above the ask. If bid/ask unavailable, return None.
    """
    if bid is None or ask is None or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    spread = ask - bid
    limit = mid + 0.40 * spread
    limit = min(limit, ask)
    return round(limit, 2)


def check_wheel_short_put(
    position: dict,
    option_snapshot: dict,
) -> Optional[ForceCloseResult]:
    """Force-close check for a wheel SHORT_PUT position.

    position: dict with keys including `symbol`, `qty` (<0 for short)
    option_snapshot: dict with keys `delta`, `dte`, `bid`, `ask`

    Returns ForceCloseResult if trigger fires, else None.
    """
    if not _is_enabled():
        return None
    delta = option_snapshot.get("delta")
    dte = option_snapshot.get("dte")
    if delta is None or dte is None:
        return None
    if abs(delta) >= DEEP_ITM_ABS_DELTA and dte <= NEAR_EXPIRY_DTE:
        return ForceCloseResult(
            rule_code="WHEEL_SHORT_PUT_DEEP_ITM_EXPIRING",
            reason=(
                f"Short put abs(delta)={abs(delta):.2f} >= {DEEP_ITM_ABS_DELTA} "
                f"AND DTE={dte} <= {NEAR_EXPIRY_DTE}. Force-close to avoid "
                f"unfavorable expiry dynamics."
            ),
            suggested_limit_price=_compute_close_limit_buy_to_close(
                option_snapshot.get("bid"), option_snapshot.get("ask"),
            ),
        )
    return None


def check_credit_spread_short_leg(
    strategy_type: str,
    short_leg_snapshot: dict,
) -> Optional[ForceCloseResult]:
    """Force-close check for the short leg of any credit spread.

    strategy_type: one of "bull_put_spread", "bear_call_spread", "iron_condor"
    short_leg_snapshot: dict with keys `delta`, `dte`
    """
    if not _is_enabled():
        return None
    code_map = {
        "bull_put_spread": "BPS_SHORT_LEG_DEEP_ITM_EXPIRING",
        "bear_call_spread": "BCS_SHORT_LEG_DEEP_ITM_EXPIRING",
        "iron_condor": "IC_SHORT_LEG_DEEP_ITM_EXPIRING",
    }
    if strategy_type not in code_map:
        return None
    delta = short_leg_snapshot.get("delta")
    dte = short_leg_snapshot.get("dte")
    if delta is None or dte is None:
        return None
    if abs(delta) >= DEEP_ITM_ABS_DELTA and dte <= NEAR_EXPIRY_DTE:
        return ForceCloseResult(
            rule_code=code_map[strategy_type],
            reason=(
                f"{strategy_type} short leg abs(delta)={abs(delta):.2f} >= "
                f"{DEEP_ITM_ABS_DELTA} AND DTE={dte} <= {NEAR_EXPIRY_DTE}. "
                f"Force-close entire spread."
            ),
            suggested_limit_price=None,  # Spread pricing handled by caller
        )
    return None
