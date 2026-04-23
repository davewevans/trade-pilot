"""Utility helpers for option-chain fetching."""


def clamp_strike_range(
    spot_price: float,
    side: str,
    clamp_pct: float = 0.25,
) -> tuple[float, float]:
    """Return (min_strike, max_strike) clamped around spot for option-chain queries.

    For puts:  strikes in [spot * (1 - clamp_pct), spot * 1.05]
    For calls: strikes in [spot * 0.95, spot * (1 + clamp_pct)]

    The 5% ITM buffer keeps slightly-ITM contracts available (e.g. CCs above
    cost basis on a recovered stock).

    Default clamp_pct=0.25 means ±25% of spot — intentionally generous to
    avoid clipping delta-0.20 to delta-0.30 contracts. If lowered to 0.10 on
    a low-vol name the delta band could be clipped; check before adjusting.

    Returns (0.0, inf) when spot is unknown or <= 0 (no clamp applied).
    """
    if spot_price is None or spot_price <= 0:
        return (0.0, float("inf"))
    if side == "put":
        return (spot_price * (1.0 - clamp_pct), spot_price * 1.05)
    elif side == "call":
        return (spot_price * 0.95, spot_price * (1.0 + clamp_pct))
    return (0.0, float("inf"))
