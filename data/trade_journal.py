"""Append-only trade journal backed by a JSONL file."""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _PROJECT_ROOT / "data" / "journal.jsonl"


class TradeJournal:
    """Append-only trade journal stored as JSONL (one JSON object per line)."""

    def __init__(self, path: Path | None = None):
        self.path = path or _DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict) -> None:
        """Append a new entry with an automatic timestamp.

        Expected entry keys: symbol, underlying, wheel_state, action,
        contract_symbol, qty, limit_price, confidence, reasoning,
        order_id, status, fill_price, pnl, closed_at.
        """
        record = {"timestamp": datetime.now().isoformat(timespec="seconds")}
        record.update(entry)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        logger.info("Journal append: %s %s %s", record.get("action"), record.get("symbol"), record.get("order_id"))

    def _read_all(self) -> list[dict]:
        """Read every line from the journal file."""
        if not self.path.exists():
            return []
        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed journal line %d", lineno)
        return entries

    def get_recent(self, symbol: str, n: int = 5) -> list[dict]:
        """Return the last *n* entries for *symbol*, newest first."""
        entries = self._read_all()
        matching = [
            e for e in entries
            if e.get("underlying", "").upper() == symbol.upper()
            or e.get("symbol", "").upper() == symbol.upper()
        ]
        return list(reversed(matching[-n:]))

    def get_open_positions(self, symbol: str) -> list[dict]:
        """Return entries where status is 'submitted' or 'filled' and not yet closed."""
        entries = self._read_all()
        return [
            e for e in entries
            if (
                e.get("status") in ("submitted", "filled")
                and e.get("closed_at") is None
                and (
                    e.get("underlying", "").upper() == symbol.upper()
                    or e.get("symbol", "").upper() == symbol.upper()
                )
            )
        ]

    def update(self, order_id: str, updates: dict) -> None:
        """Update fields on the entry matching *order_id* and rewrite the file."""
        entries = self._read_all()
        found = False
        for entry in entries:
            if entry.get("order_id") == order_id:
                entry.update(updates)
                found = True
                break
        if not found:
            logger.warning("Journal update: order_id %s not found", order_id)
            return
        with open(self.path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, default=str) + "\n")
        logger.info("Journal update: order_id %s updated with %s", order_id, list(updates.keys()))

    def format_for_prompt(self, symbol: str, n: int = 5) -> str:
        """Format recent trades as a string suitable for Claude's context.

        Returns an empty string if no trades exist for the symbol.
        """
        recent = self.get_recent(symbol, n)
        if not recent:
            return ""

        lines = [f"Last {len(recent)} trades on {symbol.upper()}:"]
        for e in recent:
            date = e.get("timestamp", "?")[:10]
            contract = e.get("contract_symbol") or e.get("symbol") or "?"
            action = e.get("action", "?")
            price = e.get("limit_price")
            price_str = f"${price}" if price is not None else "?"
            confidence = e.get("confidence", "?")
            status = e.get("status", "?")

            # Build PnL suffix
            pnl = e.get("pnl")
            pnl_str = ""
            if pnl is not None:
                sign = "+" if pnl >= 0 else ""
                pnl_str = f" -> PnL: {sign}${pnl}"

            lines.append(
                f"- {date}: {action} {contract} @ {price_str} "
                f"(confidence: {confidence}) -> {status}{pnl_str}"
            )

        body = "\n".join(lines)
        return f"<recent_trades>\n{body}\n</recent_trades>"
