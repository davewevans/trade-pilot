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

        Optional context/decision fields (log what is available):
            iv_rank: IV rank at time of decision (float, 0-100)
            iv_environment: LOW / MODERATE / HIGH
            delta: delta of the selected contract (float)
            dte: days to expiration of the selected contract (int)
            vix: VIX value at time of decision (float)
            market_regime: confirmed regime at time of decision (str)
            strategy_type: which strategy made this decision —
                "wheel_csp", "wheel_cc", "iron_condor",
                "bull_put_spread", "bear_call_spread",
                "long_call_vertical"
            skip_reason: if action is skip, the reason string
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

    def get_symbol_stats(self, symbol: str, days: int = 30) -> dict:
        """Return aggregate performance stats for a symbol over the past N days.

        Used to give Claude feedback on its own recent decision quality.
        Returns a dict suitable for inclusion in the Claude context.
        """
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        entries = self._read_all()
        relevant = [
            e for e in entries
            if (
                e.get("underlying", "").upper() == symbol.upper()
                and e.get("timestamp", "") >= cutoff
            )
        ]

        if not relevant:
            return {
                "symbol": symbol.upper(),
                "lookback_days": days,
                "total_decisions": 0,
                "trades": 0,
                "skips": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": None,
                "avg_iv_rank_at_entry": None,
                "avg_delta_at_entry": None,
                "total_pnl": 0.0,
                "note": "No history in lookback window",
            }

        trades = [e for e in relevant if e.get("action") not in ("skip", "hold")]
        skips = [e for e in relevant if e.get("action") in ("skip", "hold")
                 or e.get("status") == "skipped"]
        closed = [e for e in trades if e.get("closed_at") and e.get("pnl") is not None]
        wins = [e for e in closed if float(e.get("pnl", 0)) > 0]
        losses = [e for e in closed if float(e.get("pnl", 0)) <= 0]

        iv_ranks = [float(e["iv_rank"]) for e in trades if e.get("iv_rank") is not None]
        deltas = [float(e["delta"]) for e in trades if e.get("delta") is not None]
        total_pnl = sum(float(e.get("pnl", 0)) for e in closed)

        win_rate = round(len(wins) / len(closed) * 100, 1) if closed else None
        avg_ivr = round(sum(iv_ranks) / len(iv_ranks), 1) if iv_ranks else None
        avg_delta = round(sum(deltas) / len(deltas), 3) if deltas else None

        return {
            "symbol": symbol.upper(),
            "lookback_days": days,
            "total_decisions": len(relevant),
            "trades": len(trades),
            "skips": len(skips),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "avg_iv_rank_at_entry": avg_ivr,
            "avg_delta_at_entry": avg_delta,
            "total_pnl": round(total_pnl, 2),
        }

    def format_stats_for_prompt(self, symbol: str, days: int = 30) -> str:
        """Format symbol performance stats as a string for Claude's context."""
        stats = self.get_symbol_stats(symbol, days)

        if stats["total_decisions"] == 0:
            return ""

        win_str = f"{stats['win_rate']}%" if stats['win_rate'] is not None else "n/a"
        ivr_str = str(stats['avg_iv_rank_at_entry']) if stats['avg_iv_rank_at_entry'] else "n/a"
        delta_str = str(stats['avg_delta_at_entry']) if stats['avg_delta_at_entry'] else "n/a"
        pnl_sign = "+" if stats['total_pnl'] >= 0 else ""

        lines = [
            f"Performance on {symbol.upper()} (last {days} days):",
            f"  Decisions: {stats['total_decisions']} "
            f"({stats['trades']} trades, {stats['skips']} skips)",
            f"  Win rate: {win_str} ({stats['wins']}W / {stats['losses']}L)",
            f"  Avg IV rank at entry: {ivr_str}",
            f"  Avg delta at entry: {delta_str}",
            f"  Total P&L: {pnl_sign}${stats['total_pnl']}",
        ]
        body = "\n".join(lines)
        return f"<performance_stats>\n{body}\n</performance_stats>"

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
