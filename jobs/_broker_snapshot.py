"""Broker-truth snapshot used by reconciliation jobs.

Fetches per-account positions, orders, cash, buying_power, portfolio_value
from every account in STRATEGY_ACCOUNT_MAP (deduplicated by credentials)
and returns a normalised dict. Pure read-only — never places or modifies
orders. Failures are captured per-account; one account's failure must
not break the others.

Account-name assignment rules
------------------------------
For groups with a single strategy the account_name equals the strategy key.
For the three-strategy PAPER1 group (bull_put_spread, bear_call_spread,
long_call_vertical), the account_name is "spreads_shared" — none of the
three keys share a common prefix so an explicit special case is used.
For any other multi-strategy group: if a common name prefix exists it is
used (trailing underscores stripped); otherwise the alphabetically-first
strategy key is used.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# The PAPER1 group of three strategies that share one account.
_SPREADS_SHARED_STRATEGIES = frozenset({
    "bull_put_spread", "bear_call_spread", "long_call_vertical",
})


@dataclass
class AccountSnapshot:
    account_name: str                # e.g. "wheel", "turnover_wheel", "spreads_shared"
    strategy_keys: list[str]         # strategies that use this account's credentials
    fetched_ok: bool
    error: str | None = None
    portfolio_value: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    # Normalised positions — each dict has at minimum:
    #   symbol, qty (signed: negative for short), side, asset_class,
    #   avg_entry_price, market_value, unrealized_pl, current_price
    positions: list[dict] = field(default_factory=list)
    # Normalised open orders — at minimum: id, symbol, qty, side, status,
    # limit_price, order_type, submitted_at
    open_orders: list[dict] = field(default_factory=list)


def _derive_account_name(strategy_keys: list[str]) -> str:
    """Derive a short account name from a list of strategy keys sharing credentials."""
    key_set = frozenset(strategy_keys)
    if key_set == _SPREADS_SHARED_STRATEGIES:
        return "spreads_shared"
    sorted_keys = sorted(strategy_keys)
    if len(sorted_keys) == 1:
        return sorted_keys[0]
    # Try a common prefix
    import os
    prefix = os.path.commonprefix(sorted_keys).rstrip("_")
    if prefix:
        return prefix
    return sorted_keys[0]


def fetch_all_accounts() -> list[AccountSnapshot]:
    """Return one AccountSnapshot per distinct account in STRATEGY_ACCOUNT_MAP.

    Deduplication is by (api_key, secret_key). Each snapshot's
    strategy_keys lists every strategy that shares those credentials.
    Per-account failures are captured on the dataclass; this function
    itself never raises.
    """
    from brokers.broker_factory import make_broker_cached
    from config import settings

    # Group strategies by (api_key, secret_key)
    creds_to_strategies: dict[tuple[str, str], list[str]] = {}
    for strategy_name, (key_var, secret_var) in settings.STRATEGY_ACCOUNT_MAP.items():
        api_key = getattr(settings, key_var, "") or ""
        secret_key = getattr(settings, secret_var, "") or ""
        if not api_key:
            continue
        cred_pair = (api_key, secret_key)
        creds_to_strategies.setdefault(cred_pair, []).append(strategy_name)

    snapshots: list[AccountSnapshot] = []

    for (api_key, secret_key), strategy_keys in creds_to_strategies.items():
        account_name = _derive_account_name(strategy_keys)
        snap = AccountSnapshot(
            account_name=account_name,
            strategy_keys=sorted(strategy_keys),
            fetched_ok=False,
        )
        try:
            broker = make_broker_cached(api_key, secret_key)

            acct = broker.get_account()
            snap.portfolio_value = _to_float(acct.get("portfolio_value"))
            snap.cash = _to_float(acct.get("cash"))
            snap.buying_power = _to_float(acct.get("buying_power"))

            # Positions — prefer get_all_positions() (equity + options)
            try:
                raw_positions = broker.get_all_positions()
            except AttributeError:
                raw_positions = broker.get_positions()

            snap.positions = _normalise_positions(raw_positions)

            try:
                raw_orders = broker.get_orders(status="open")
                snap.open_orders = _normalise_orders(raw_orders or [])
            except Exception as _order_exc:
                logger.warning(
                    "_broker_snapshot: get_orders failed for %s: %s",
                    account_name, _order_exc,
                )
                snap.open_orders = []

            snap.fetched_ok = True

        except Exception as exc:
            snap.fetched_ok = False
            snap.error = str(exc)[:200]
            logger.error(
                "_broker_snapshot: fetch failed for account %s (strategies %s): %s",
                account_name, strategy_keys, exc,
            )

        snapshots.append(snap)

    return snapshots


# ── normalisation helpers ─────────────────────────────────────────────────────

def _normalise_positions(raw: list[dict]) -> list[dict]:
    """Return a list of position dicts with standardised keys and signed qty."""
    result = []
    for p in (raw or []):
        try:
            qty_raw = p.get("qty") or p.get("quantity") or 0
            qty = float(qty_raw)
            # Alpaca already returns negative qty for short positions on options.
            # Ensure market_value, current_price defaults.
            result.append({
                "symbol":          (p.get("symbol") or "").upper(),
                "qty":             qty,
                "side":            p.get("side", "long" if qty >= 0 else "short"),
                "asset_class":     (p.get("asset_class") or "").lower(),
                "avg_entry_price": _to_float(p.get("avg_entry_price")),
                "market_value":    _to_float(p.get("market_value")),
                "unrealized_pl":   _to_float(p.get("unrealized_pl")),
                "current_price":   _to_float(
                    p.get("current_price") or p.get("lastday_price")
                ),
                "_raw": p,
            })
        except Exception:
            logger.debug("_broker_snapshot: skipping malformed position %s", p)
    return result


def _normalise_orders(raw: list[dict]) -> list[dict]:
    """Return a list of order dicts with standardised keys."""
    result = []
    for o in (raw or []):
        try:
            result.append({
                "id":           str(o.get("id") or o.get("order_id") or ""),
                "symbol":       (o.get("symbol") or "").upper(),
                "qty":          _to_float(o.get("qty") or o.get("quantity")),
                "side":         o.get("side", ""),
                "status":       (o.get("status") or "").lower(),
                "limit_price":  _to_float(o.get("limit_price")),
                "order_type":   o.get("order_type", ""),
                "submitted_at": o.get("submitted_at", ""),
                "_raw": o,
            })
        except Exception:
            logger.debug("_broker_snapshot: skipping malformed order %s", o)
    return result


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
