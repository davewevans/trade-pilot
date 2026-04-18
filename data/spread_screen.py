"""Pre-filter spread candidates using cheap/cached data before ctx_builder.build() calls.

Applies strategy hard gates (regime, IVR, earnings) using batch API calls,
reducing expensive ctx_builder.build() + ORATS /strikes calls by ~70% on typical days.

Gates checked here (cheap):
  1. Regime gate (free — no API call)
  2. IVR gate   (batched /ivrank — 1 ORATS call per ≤10 symbols)
  3. Earnings gate (non-ORATS Finnhub/yfinance, per IVR-surviving symbol only)

Gates NOT checked here (handled downstream by ctx_builder + pre_check_entry):
  above_sma_50  — requires 50-day price history (yfinance, not in batch result)
  iv_hv_ratio   — requires historical ORATS data (full context needed)
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


# ── Gate thresholds ───────────────────────────────────────────────────────────
# Canonical source — must stay in sync with each strategy's _check_entry_conditions().
# When a strategy threshold changes, update the corresponding value here.
#
#   bull_put_spread_strategy.py:249,258
#   bear_call_spread_strategy.py:240,245
#   iron_condor_strategy.py:209,219
#   long_call_vertical_strategy.py:227,252

STRATEGY_GATES: dict[str, dict] = {
    "bull_put_spread": {
        "allowed_regimes": frozenset({"NEUTRAL", "BULL"}),
        "ivr_min": 35,
        "ivr_max": None,
        "earnings_min_days": 25,
    },
    "bear_call_spread": {
        "allowed_regimes": frozenset({"BEAR", "NEUTRAL"}),
        "ivr_min": 40,
        "ivr_max": None,
        "earnings_min_days": 25,
    },
    "iron_condor": {
        "allowed_regimes": frozenset({"NEUTRAL"}),
        "ivr_min": 50,
        "ivr_max": None,
        "earnings_min_days": 35,
    },
    "long_call_vertical": {
        "allowed_regimes": frozenset({"BULL"}),
        "ivr_min": None,
        "ivr_max": 30,
        "earnings_min_days": 65,
    },
}


def screen_spread_candidates(
    symbols: list[str],
    strategy_name: str,
    regime: str,
    iv_env: str,
    orats_client,
    earnings_fn: Callable[[str], dict] | None = None,
) -> tuple[list[str], list[dict]]:
    """Apply hard gates using cheap/batch data before ctx_builder.build().

    Args:
        symbols:       Ordered list of watchlist symbols to screen.
        strategy_name: One of the keys in STRATEGY_GATES.
        regime:        Current confirmed market regime string (e.g. "NEUTRAL").
        iv_env:        Current IV environment string (e.g. "HIGH").  Reserved for
                       future gates; not currently used in screening.
        orats_client:  ORATSClient instance (used for get_iv_rank_batch).
        earnings_fn:   Callable(symbol) -> dict with "days_to_earnings" key.
                       Pass None to skip the earnings gate.

    Returns:
        (survivors, rejection_log)
        survivors:      Symbols that passed all pre-filter gates, in stable input order.
        rejection_log:  List of rejection dicts:
                        {"symbol", "reason", "value", "threshold"}
    """
    if strategy_name not in STRATEGY_GATES:
        logger.warning(
            "screen_spread_candidates: unknown strategy %r — skipping pre-filter, returning all %d symbols",
            strategy_name, len(symbols),
        )
        return list(symbols), []

    if not symbols:
        return [], []

    gates = STRATEGY_GATES[strategy_name]
    rejection_log: list[dict] = []

    # ── Gate 1: Regime (free — no API call) ──────────────────────────────────
    if regime not in gates["allowed_regimes"]:
        for sym in symbols:
            rejection_log.append({
                "symbol": sym,
                "reason": "regime_mismatch",
                "value": regime,
                "threshold": sorted(gates["allowed_regimes"]),
            })
        return [], rejection_log

    # ── Gate 2: IVR (batch /ivrank — 1 call per ≤10 symbols) ─────────────────
    ivr_data: dict[str, dict] = {}
    try:
        ivr_data = orats_client.get_iv_rank_batch(symbols)
    except Exception:
        logger.warning(
            "screen_spread_candidates: get_iv_rank_batch failed for strategy %r "
            "— passing all %d symbols through IVR gate",
            strategy_name, len(symbols), exc_info=True,
        )

    ivr_survivors: list[str] = []
    for sym in symbols:
        entry = ivr_data.get(sym.upper(), {})
        ivr = entry.get("ivRank1y")

        if ivr is None:
            # Missing data — conservative: let ctx_builder decide
            ivr_survivors.append(sym)
            continue

        if gates["ivr_min"] is not None and ivr < gates["ivr_min"]:
            rejection_log.append({
                "symbol": sym,
                "reason": "ivr_below_threshold",
                "value": round(ivr, 1),
                "threshold": gates["ivr_min"],
            })
        elif gates["ivr_max"] is not None and ivr >= gates["ivr_max"]:
            rejection_log.append({
                "symbol": sym,
                "reason": "ivr_above_threshold",
                "value": round(ivr, 1),
                "threshold": gates["ivr_max"],
            })
        else:
            ivr_survivors.append(sym)

    # ── Gate 3: Earnings (non-ORATS, only for IVR survivors) ──────────────────
    if earnings_fn is None:
        return ivr_survivors, rejection_log

    survivors: list[str] = []
    for sym in ivr_survivors:
        try:
            earnings = earnings_fn(sym)
            dte = earnings.get("days_to_earnings")

            if dte is not None and dte <= gates["earnings_min_days"]:
                rejection_log.append({
                    "symbol": sym,
                    "reason": "earnings_too_close",
                    "value": dte,
                    "threshold": gates["earnings_min_days"],
                })
            else:
                survivors.append(sym)
        except Exception:
            logger.warning(
                "screen_spread_candidates: earnings check failed for %s — passing through",
                sym, exc_info=True,
            )
            survivors.append(sym)

    return survivors, rejection_log
