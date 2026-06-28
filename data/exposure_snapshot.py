"""Consolidated cross-account exposure snapshot for the Hermes risk monitor.

Hermes runs an advisory cross-account risk monitor on a separate VPS with no
Alpaca credentials — the bot is the sole broker-talker. This module assembles a
read-only, JSON-serializable view of the bot's current cross-account exposure so
the publisher (``data/report_publisher.py``) can commit it to the reports repo:

  - ``accounts``:       per ``paper_N``, the open positions from
                        ``portfolio_paper_N.json`` with derived ``root`` + ``sector``.
  - ``book_exposure``:  the existing ``compute_cross_account_book_exposure()``
                        by-underlying family / anti-crowding view.
  - ``computed_at``:    ISO8601 ET timestamp of assembly.
  - ``sources``:        per-account snapshot timestamps + mtimes, so a stale or
                        missing snapshot is visible to Hermes rather than silent.

This is **read-only aggregation** — it changes no guardrail, anti-crowding, or
trade logic, makes no broker call, and writes nothing to trading state. Every
per-file read is swallowed (missing snapshot -> that account empty); the builder
never raises.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import settings
from utils.occ import extract_root

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def _derive_root(symbol: str, underlying: str) -> str:
    """Best-effort underlying root for a position.

    Prefer the snapshot's own ``underlying`` field; fall back to parsing the OCC
    symbol; finally fall back to the raw symbol (equity rows are a plain ticker).
    """
    if underlying:
        return underlying.upper()
    occ_root = extract_root(symbol)
    if occ_root:
        return occ_root.upper()
    return (symbol or "").upper()


def _sector_for(root: str) -> str:
    """Map a root ticker to its sector; unknown -> ``"unknown"``."""
    return settings.SYMBOL_SECTORS.get(root, "unknown")


def _load_account_snapshot(account_id: str) -> tuple[dict | None, str | None]:
    """Read ``portfolio_<account_id>.json``. Never raises.

    Returns ``(parsed_dict_or_None, mtime_iso_or_None)``. A missing or unreadable
    file yields ``(None, None)`` so the caller renders that account empty.
    """
    path = settings.SNAPSHOTS_DIR / f"portfolio_{account_id}.json"
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mtime = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds")
        return data, mtime
    except Exception:
        logger.warning("exposure_snapshot: failed to read %s", path, exc_info=True)
        return None, None


def _account_positions(snapshot: dict | None) -> list[dict]:
    """Extract the position rows we publish from a per-account snapshot.

    Each row is reduced to the fields Hermes needs for sector/beta attribution:
    ``symbol``, ``qty``, ``side``, derived ``root``, derived ``sector``.
    """
    if not snapshot:
        return []
    out: list[dict] = []
    for p in snapshot.get("positions", []) or []:
        symbol = (p.get("symbol") or "").upper()
        underlying = (p.get("underlying") or "")
        root = _derive_root(symbol, underlying)
        out.append({
            "symbol": symbol,
            "qty": p.get("quantity"),
            "side": str(p.get("side") or "").lower(),
            "root": root,
            "sector": _sector_for(root),
        })
    return out


def build_exposure_snapshot() -> dict:
    """Assemble the consolidated cross-account exposure snapshot.

    Read-only and defensive: enumerates active accounts, reads each per-account
    portfolio snapshot (missing -> empty), derives root + sector per position,
    and folds in the existing book-level exposure view. Never raises — on any
    unexpected failure it returns whatever was assembled so far.
    """
    computed_at = datetime.now(_ET).isoformat(timespec="seconds")

    accounts: dict[str, dict] = {}
    account_sources: dict[str, str | None] = {}

    try:
        from data.account_manager import AccountManager

        active = AccountManager().get_active_accounts()
    except Exception:
        logger.warning("exposure_snapshot: failed to enumerate accounts", exc_info=True)
        active = {}

    for account_id, cfg in active.items():
        snapshot, mtime = _load_account_snapshot(account_id)
        accounts[account_id] = {
            "strategy": (cfg or {}).get("strategy", ""),
            "label": (cfg or {}).get("label", ""),
            "snapshot_timestamp": (snapshot or {}).get("timestamp"),
            "positions": _account_positions(snapshot),
        }
        account_sources[account_id] = mtime

    try:
        from data.book_exposure import compute_cross_account_book_exposure

        book = compute_cross_account_book_exposure()
    except Exception:
        # compute_* is already internally defensive, but the snapshot must never
        # fail to build just because the book view did.
        logger.warning("exposure_snapshot: book exposure unavailable", exc_info=True)
        book = {}

    return {
        "computed_at": computed_at,
        "accounts": accounts,
        "book_exposure": book,
        "sources": {
            "account_snapshots": account_sources,
            "book_exposure": book.get("sources") if isinstance(book, dict) else None,
        },
    }
