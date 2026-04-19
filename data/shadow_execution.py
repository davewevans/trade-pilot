"""Shadow execution — measurement-only NBBO capture around every order submit.

This module is instrumentation. It never places, modifies, cancels, or
reconciles orders. It never raises to its callers; all public functions
are wrapped in try/except and return None on any failure.

Feature is gated by settings.SHADOW_EXECUTION_ENABLED.

WHAT THE SCORE ACTUALLY MEASURES
────────────────────────────────
Limit prices in trade-pilot are decided using Alpaca's quote data, which on
paper accounts is indicative and 15-minute delayed. The shadow-execution
measurement compares that limit against ORATS real-time NBBO. That means the
score is a blended signal, not a pure microstructure measure: a `not_fillable`
classification does not distinguish between (i) the strategy genuinely
submitting unreachable prices, (ii) the stale Alpaca feed producing a limit
that was reasonable against delayed data but unreachable against real quotes,
and (iii) true illiquidity in the contract. This is still the right thing to
measure for the real-money gate question, but it is not a clean measurement
of any single failure mode.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET_ZONE = ZoneInfo("America/New_York")


def classify_fillability(
    is_credit: bool,
    limit_magnitude: float,
    net_bid: float | None,
    net_mid: float | None,
    net_ask: float | None,
) -> str:
    """Return one of:
        'always_fillable' | 'sometimes_fillable' | 'not_fillable' | 'data_unavailable'

    SIGN CONVENTION (critical — get this right)
    ────────────────────────────────────────────
    `limit_magnitude` is always non-negative. It represents:
      - is_credit=True  → the credit amount the SELLER wants to receive. Larger is
                          harder to fill (asking for more premium).
      - is_credit=False → the debit the BUYER is willing to pay. Larger is easier
                          to fill (willing to pay more).

    CREDIT classification (seller):
        limit_magnitude <= net_mid              → always_fillable
        net_mid <  limit_magnitude <= net_ask   → sometimes_fillable
        limit_magnitude >  net_ask              → not_fillable

    DEBIT classification (buyer):
        limit_magnitude >= net_mid              → always_fillable
        net_bid <= limit_magnitude <  net_mid   → sometimes_fillable
        limit_magnitude <  net_bid              → not_fillable

    BOUNDARY BEHAVIOR (unit-tested):
        Exactly at mid          → always_fillable  (both credit and debit)
        Exactly at ask (credit) → sometimes_fillable
        Exactly at bid (debit)  → sometimes_fillable

    DATA_UNAVAILABLE is a distinct bucket from NOT_FILLABLE:
      - If any of net_bid / net_mid / net_ask is None → 'data_unavailable'
      - If the quote is crossed (net_bid > net_ask)   → 'data_unavailable' (log WARNING)
      - If the quote is locked and degenerate (net_bid == net_ask == net_mid == 0)
                                                      → 'data_unavailable'

    The data_unavailable bucket exists because missing-quote cases (illiquid
    strike, halt, expiration-day weirdness) are a different failure mode than
    a genuinely unreachable limit, and collapsing them into 'not_fillable'
    pollutes the fill-realism score.
    """
    if net_bid is None or net_mid is None or net_ask is None:
        return "data_unavailable"
    if net_bid > net_ask:
        logger.warning(
            "classify_fillability: crossed quote net_bid=%.4f > net_ask=%.4f",
            net_bid, net_ask,
        )
        return "data_unavailable"
    if net_bid == 0 and net_ask == 0 and net_mid == 0:
        return "data_unavailable"

    if is_credit:
        if limit_magnitude <= net_mid:
            return "always_fillable"
        if limit_magnitude <= net_ask:
            return "sometimes_fillable"
        return "not_fillable"
    else:
        if limit_magnitude >= net_mid:
            return "always_fillable"
        if limit_magnitude >= net_bid:
            return "sometimes_fillable"
        return "not_fillable"


def _fetch_leg_quotes(legs: list[dict]) -> dict[str, dict]:
    """Fetch bid/ask/mid for every leg symbol. Returns dict[contract_symbol → quote].

    Tries ORATS first (via get_strikes_by_delta canonical cache), falls back to
    Alpaca for any leg not covered. Records source per leg as 'orats'/'alpaca'/'unavailable'.
    """
    from utils.occ import parse_occ, extract_root

    # Group legs by (underlying, option_type) for batching
    groups: dict[tuple[str, str], list[dict]] = {}
    for leg in legs:
        parsed = parse_occ(leg["contract_symbol"])
        if not parsed:
            continue
        underlying = parsed["root"]
        opt_type = "put" if parsed["option_type"] == "P" else "call"
        key = (underlying, opt_type)
        groups.setdefault(key, []).append({**leg, "_parsed": parsed})

    results: dict[str, dict] = {}

    # ORATS lookup per (underlying, option_type) group
    orats_hits: set[str] = set()
    try:
        from data.orats_client import ORATSClient
        orats_client = ORATSClient()
        for (underlying, opt_type), group_legs in groups.items():
            try:
                # Use canonical range — will be a cache hit if market_open ran recently
                strikes = orats_client.get_strikes_by_delta(
                    symbol=underlying,
                    option_type=opt_type,
                    delta_min=0.10,
                    delta_max=0.70,
                    dte_min=14,
                    dte_max=60,
                )
                # Index by (expiration_date, strike)
                orats_index: dict[tuple[str, float], dict] = {}
                for s in strikes:
                    exp = s.get("expiration_date", "")
                    strike = s.get("strike")
                    if exp and strike is not None:
                        orats_index[(exp, float(strike))] = s
            except Exception:
                logger.debug("ORATS fetch failed for %s %s", underlying, opt_type, exc_info=True)
                orats_index = {}

            for leg_info in group_legs:
                sym = leg_info["contract_symbol"]
                parsed = leg_info["_parsed"]
                exp_str = parsed["expiration_str"]
                strike = float(parsed["strike"])
                hit = orats_index.get((exp_str, strike))
                if hit:
                    bid = hit.get("bid_price")
                    ask = hit.get("ask_price")
                    mid = hit.get("mid_price")
                    if bid is None and ask is None:
                        continue
                    if mid is None and bid is not None and ask is not None:
                        mid = round((bid + ask) / 2, 4)
                    results[sym] = {"bid": bid, "ask": ask, "mid": mid, "source": "orats"}
                    orats_hits.add(sym)
    except Exception:
        logger.debug("ORATS client init failed in shadow_execution", exc_info=True)

    # Alpaca fallback for any leg not covered by ORATS
    alpaca_needed = [
        leg["contract_symbol"]
        for leg in legs
        if leg["contract_symbol"] not in orats_hits
    ]
    if alpaca_needed:
        try:
            from brokers.broker_factory import get_broker
            broker = get_broker()
            # Group by underlying for the broker call
            underlying_for_batch = extract_root(alpaca_needed[0]) if alpaca_needed else None
            snapshots = broker.get_option_snapshots(alpaca_needed, underlying=underlying_for_batch)
            for sym in alpaca_needed:
                snap = snapshots.get(sym) if snapshots else None
                if snap:
                    bid = snap.get("bid")
                    ask = snap.get("ask")
                    mid = snap.get("mid")
                    if mid is None and bid is not None and ask is not None:
                        mid = round((bid + ask) / 2, 4)
                    results[sym] = {"bid": bid, "ask": ask, "mid": mid, "source": "alpaca"}
                else:
                    results[sym] = {"bid": None, "ask": None, "mid": None, "source": "unavailable"}
        except Exception:
            logger.debug("Alpaca snapshot fallback failed in shadow_execution", exc_info=True)
            for sym in alpaca_needed:
                if sym not in results:
                    results[sym] = {"bid": None, "ask": None, "mid": None, "source": "unavailable"}

    # Mark any leg we never touched as unavailable
    for leg in legs:
        sym = leg["contract_symbol"]
        if sym not in results:
            results[sym] = {"bid": None, "ask": None, "mid": None, "source": "unavailable"}

    return results


def _compute_net_nbbo(
    legs: list[dict],
    quotes: dict[str, dict],
) -> tuple[float | None, float | None, float | None]:
    """Compute net spread bid/mid/ask given per-leg quotes and position_intent.

    Short legs (receive premium): sell_to_open or buy_to_close
    Long legs (pay premium): buy_to_open or sell_to_close

    For single-leg orders the formula degenerates to just the leg's own quote.
    """
    _SHORT_INTENTS = {"sell_to_open", "buy_to_close"}

    short_bids: list[float] = []
    short_asks: list[float] = []
    short_mids: list[float] = []
    long_bids: list[float] = []
    long_asks: list[float] = []
    long_mids: list[float] = []

    for leg in legs:
        sym = leg["contract_symbol"]
        q = quotes.get(sym, {})
        bid = q.get("bid")
        ask = q.get("ask")
        mid = q.get("mid")
        intent = leg.get("position_intent", "")
        if intent in _SHORT_INTENTS:
            if bid is None or ask is None or mid is None:
                return None, None, None
            short_bids.append(bid)
            short_asks.append(ask)
            short_mids.append(mid)
        else:
            if bid is None or ask is None or mid is None:
                return None, None, None
            long_bids.append(bid)
            long_asks.append(ask)
            long_mids.append(mid)

    net_bid = sum(short_bids) - sum(long_asks)
    net_ask = sum(short_asks) - sum(long_bids)
    net_mid = sum(short_mids) - sum(long_mids)
    return round(net_bid, 4), round(net_mid, 4), round(net_ask, 4)


def record_submission(
    *,
    strategy_type: str,
    action: str,
    legs: list[dict],
    net_limit_price: float,
    alpaca_order_id: str | None,
    submitted_at_utc: datetime | None = None,
) -> int | None:
    """Capture t0 NBBO, classify, insert one parent row + N leg rows.

    Returns the parent row id on success, None on any failure or when
    SHADOW_EXECUTION_ENABLED is False. Never raises.

    `legs` dicts must have: contract_symbol, leg_role, side, position_intent.
    `net_limit_price` is signed: negative=credit, positive=debit.
    """
    from config import settings
    if not settings.SHADOW_EXECUTION_ENABLED:
        return None

    try:
        return _record_submission_inner(
            strategy_type=strategy_type,
            action=action,
            legs=legs,
            net_limit_price=net_limit_price,
            alpaca_order_id=alpaca_order_id,
            submitted_at_utc=submitted_at_utc,
        )
    except Exception:
        logger.warning("shadow_execution.record_submission failed (non-fatal)", exc_info=True)
        return None


def _record_submission_inner(
    *,
    strategy_type: str,
    action: str,
    legs: list[dict],
    net_limit_price: float,
    alpaca_order_id: str | None,
    submitted_at_utc: datetime | None,
) -> int | None:
    from database.db import Database
    from database.repositories.shadow_execution import ShadowExecutionRepository
    from config import settings

    now_utc = submitted_at_utc or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(_ET_ZONE)
    submitted_at_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
    submitted_at_et_iso = now_et.strftime("%Y-%m-%dT%H:%M:%S")

    is_credit = 1 if net_limit_price < 0 else 0
    net_limit_abs = abs(net_limit_price)
    order_kind = "single_leg" if len(legs) == 1 else "multi_leg"

    # Derive underlying from first leg's OCC symbol
    underlying = None
    if legs:
        from utils.occ import extract_root
        underlying = extract_root(legs[0]["contract_symbol"])

    # Fetch NBBO for all legs
    quotes = _fetch_leg_quotes(legs)

    # Compute net spread NBBO
    net_bid, net_mid, net_ask = _compute_net_nbbo(legs, quotes)

    # Classify
    classification = classify_fillability(
        is_credit=bool(is_credit),
        limit_magnitude=net_limit_abs,
        net_bid=net_bid,
        net_mid=net_mid,
        net_ask=net_ask,
    )

    # Determine t0 status
    all_unavailable = all(
        quotes.get(leg["contract_symbol"], {}).get("source") == "unavailable"
        for leg in legs
    )
    t0_status = "failed_permanent" if all_unavailable else "captured"

    db = Database(path=str(settings.DATABASE_PATH))
    try:
        repo = ShadowExecutionRepository(db.get_connection())

        parent_row = {
            "submitted_at": submitted_at_iso,
            "submitted_at_et": submitted_at_et_iso,
            "strategy_type": strategy_type,
            "action": action,
            "underlying": underlying,
            "alpaca_order_id": alpaca_order_id,
            "order_kind": order_kind,
            "is_credit": is_credit,
            "net_limit_abs": net_limit_abs,
            "t0_status": t0_status,
            "t0_attempts": 1,
            "t0_class": classification,
            "t0_net_bid": net_bid,
            "t0_net_mid": net_mid,
            "t0_net_ask": net_ask,
            "t0_captured_at": submitted_at_iso,
        }

        leg_rows = []
        for leg in legs:
            sym = leg["contract_symbol"]
            q = quotes.get(sym, {})
            leg_rows.append({
                "contract_symbol": sym,
                "leg_role": leg.get("leg_role", "single"),
                "side": leg.get("side", ""),
                "position_intent": leg.get("position_intent"),
                "t0_bid": q.get("bid"),
                "t0_ask": q.get("ask"),
                "t0_mid": q.get("mid"),
                "t0_source": q.get("source", "unavailable"),
            })

        exec_id = repo.insert_submission(parent_row=parent_row, legs=leg_rows)
        logger.debug(
            "shadow_execution recorded: id=%d action=%s strategy=%s t0_class=%s",
            exec_id, action, strategy_type, classification,
        )
        return exec_id
    finally:
        db.close()


def fetch_leg_quotes_for_capture(legs: list[dict]) -> dict[str, dict]:
    """Public entry point for the follow-up capture job to fetch NBBO."""
    return _fetch_leg_quotes(legs)
