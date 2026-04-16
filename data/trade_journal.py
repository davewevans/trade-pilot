"""Append-only trade journal backed by a JSONL file."""

import json
import logging
from datetime import datetime
from pathlib import Path

from version import VERSION

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
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "version": VERSION,
        }
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

    def get_recent_skips(self, symbol: str, days: int = 30) -> list[dict]:
        """Return recent skip entries for a symbol within the past N days."""
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        entries = self._read_all()
        return [
            e for e in entries
            if (
                e.get("underlying", "").upper() == symbol.upper()
                and e.get("timestamp", "") >= cutoff
                # Exclude guardrail rejections (status="rejected") — those
                # are surfaced separately via format_rejections_for_prompt.
                and e.get("status") != "rejected"
                and (
                    e.get("action") in ("skip", "hold")
                    or e.get("status") == "skipped"
                )
            )
        ]

    def format_skip_history_for_prompt(self, symbol: str, days: int = 30) -> str:
        """Format recent skip history as a string for Claude's context.

        Aggregates primarily by skip_code (machine-readable) with the most
        common free-text skip_reason shown as secondary context. Older
        entries without skip_code are bucketed under OTHER.

        Helps Claude recognise persistent conditions — e.g. 12 consecutive
        LOW_IVR skips signals the IV threshold may be mis-tuned.
        """
        skips = self.get_recent_skips(symbol, days)
        if not skips:
            return ""

        from collections import Counter, defaultdict
        from strategies.skip_codes import SkipCode, normalize_skip_code

        # Group by skip_code, collecting representative reason strings
        code_counts: Counter = Counter()
        code_reasons: dict = defaultdict(Counter)
        for e in skips:
            code = normalize_skip_code(e.get("skip_code"))
            code_counts[code] += 1
            reason = (e.get("skip_reason") or e.get("reasoning") or "")[:60]
            if reason:
                code_reasons[code][reason] += 1

        lines = [f"Recent skips on {symbol.upper()} (last {days} days): {len(skips)} total"]
        for code, count in code_counts.most_common(5):
            # Append the most common free-text reason as context
            top_reason = ""
            if code_reasons[code]:
                top_reason_text = code_reasons[code].most_common(1)[0][0]
                top_reason = f": {top_reason_text}"
            lines.append(f"  x{count} {code}{top_reason}")

        body = "\n".join(lines)
        return f"<skip_history>\n{body}\n</skip_history>"

    def get_portfolio_patterns(self, days: int = 30) -> dict:
        """Analyze the full journal for portfolio-level patterns.

        Returns a dict summarizing cross-symbol patterns suitable for
        inclusion in Claude's context. Covers the past N days.
        """
        from datetime import date, timedelta
        from collections import Counter, defaultdict

        cutoff = (date.today() - timedelta(days=days)).isoformat()
        entries = self._read_all()
        recent = [e for e in entries if e.get("timestamp", "") >= cutoff]

        trades = [e for e in recent if e.get("action") not in ("skip", "hold")
                  and e.get("status") != "skipped"]
        closed = [e for e in trades if e.get("closed_at") and e.get("pnl") is not None]
        skips = [e for e in recent if e.get("action") in ("skip", "hold")
                 or e.get("status") == "skipped"]

        # Overall win rate
        wins = [e for e in closed if float(e.get("pnl", 0)) > 0]
        win_rate = round(len(wins) / len(closed) * 100, 1) if closed else None
        total_pnl = round(sum(float(e.get("pnl", 0)) for e in closed), 2)

        # Assignment rate (puts that became LONG_STOCK)
        assignments = [e for e in recent if e.get("status") == "assigned"]
        put_trades = [e for e in trades if e.get("action") == "sell_put"]
        assignment_rate = (
            round(len(assignments) / len(put_trades) * 100, 1)
            if put_trades else None
        )

        # Most common skip reasons
        skip_reasons = Counter(
            e.get("skip_reason") or "unknown" for e in skips
        )

        # Win/loss by regime
        by_regime: dict = defaultdict(lambda: {"wins": 0, "losses": 0})
        for e in closed:
            regime = e.get("market_regime", "UNKNOWN")
            if float(e.get("pnl", 0)) > 0:
                by_regime[regime]["wins"] += 1
            else:
                by_regime[regime]["losses"] += 1

        # Average IV rank at entry for winning vs losing trades
        win_ivrs = [float(e["iv_rank"]) for e in wins if e.get("iv_rank") is not None]
        loss_ivrs = [
            float(e["iv_rank"]) for e in closed
            if float(e.get("pnl", 0)) <= 0 and e.get("iv_rank") is not None
        ]
        avg_win_ivr = round(sum(win_ivrs) / len(win_ivrs), 1) if win_ivrs else None
        avg_loss_ivr = round(sum(loss_ivrs) / len(loss_ivrs), 1) if loss_ivrs else None

        # Most active underlyings
        symbol_counts = Counter(e.get("underlying", "?") for e in trades)

        return {
            "lookback_days": days,
            "generated_at": date.today().isoformat(),
            "total_decisions": len(recent),
            "total_trades": len(trades),
            "total_skips": len(skips),
            "closed_trades": len(closed),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "assignment_rate": assignment_rate,
            "top_skip_reasons": dict(skip_reasons.most_common(5)),
            "performance_by_regime": dict(by_regime),
            "avg_iv_rank_winning_trades": avg_win_ivr,
            "avg_iv_rank_losing_trades": avg_loss_ivr,
            "most_active_symbols": dict(symbol_counts.most_common(5)),
        }

    def get_recent_rejections(self, symbol: str, days: int = 30) -> list[dict]:
        """Return recent guardrail-rejection entries for *symbol* within N days.

        Guardrail rejections are written with ``status == "rejected"``
        (distinct from Claude-initiated skips which use ``status == "skipped"``).
        Returns entries newest-first.
        """
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        entries = self._read_all()
        matching = [
            e for e in entries
            if (
                e.get("underlying", "").upper() == symbol.upper()
                and e.get("timestamp", "") >= cutoff
                and e.get("status") == "rejected"
            )
        ]
        return list(reversed(matching))

    def format_rejections_for_prompt(self, symbol: str, days: int = 30) -> str:
        """Format recent guardrail rejections as an XML-wrapped block for Claude.

        Returns empty string when there are no recent rejections.
        Format example:
            <guardrail_rejections>
            Recent rejections on AAPL (last 30 days): 3 total
              2026-04-08: proposed sell_put → DELTA_OUT_OF_RANGE: delta -0.38 out of range
              2026-04-03: proposed sell_call → STRIKE_BELOW_COST_BASIS: strike below cost basis
            </guardrail_rejections>
        """
        rejections = self.get_recent_rejections(symbol, days)
        if not rejections:
            return ""

        from strategies.skip_codes import normalize_skip_code

        lines = [
            f"Recent rejections on {symbol.upper()} (last {days} days): "
            f"{len(rejections)} total"
        ]
        for e in rejections[:10]:  # cap at 10 most recent
            date_str = e.get("timestamp", "?")[:10]
            proposed = e.get("action_proposed") or e.get("action", "unknown")
            code = normalize_skip_code(e.get("skip_code"))
            reason = e.get("rejection_reason") or e.get("skip_reason") or ""
            reason_short = reason[:80] + "..." if len(reason) > 80 else reason
            lines.append(
                f"  {date_str}: proposed {proposed} \u2192 {code}: {reason_short}"
            )

        body = "\n".join(lines)
        return f"<guardrail_rejections>\n{body}\n</guardrail_rejections>"

    def get_recent_by_days(self, symbol: str, days: int = 30) -> list[dict]:
        """Return all trade entries for a symbol within the past N days.

        Unlike get_recent() which is count-bounded, this is time-bounded.
        Returns entries in chronological order (oldest first), excluding
        skips and holds — trades only.
        """
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        entries = self._read_all()
        return [
            e for e in entries
            if (
                (
                    e.get("underlying", "").upper() == symbol.upper()
                    or e.get("symbol", "").upper() == symbol.upper()
                )
                and e.get("timestamp", "") >= cutoff
                and e.get("action") not in ("skip", "hold")
                and e.get("status") != "skipped"
            )
        ]

    def format_for_prompt(self, symbol: str, days: int = 30) -> str:
        """Format recent trade history as a string for Claude's context.

        Uses a time-bounded window (default: last 30 days) rather than
        a fixed count. Returns an empty string if no trades exist.
        """
        recent = self.get_recent_by_days(symbol, days)
        if not recent:
            return ""

        lines = [f"Trade history on {symbol.upper()} (last {days} days, {len(recent)} trades):"]
        for e in recent:
            date_str = e.get("timestamp", "?")[:10]
            contract = e.get("contract_symbol") or e.get("symbol") or "?"
            action = e.get("action", "?")
            fill = e.get("fill_price")
            limit = e.get("limit_price")
            price = fill or limit
            price_str = f"${price}" if price is not None else "?"
            status = e.get("status", "?")

            # Include entry conditions if available (from Prompt 1)
            conditions = []
            iv = e.get("iv_rank")
            delta = e.get("delta")
            dte = e.get("dte")
            regime = e.get("market_regime")
            if iv is not None:
                conditions.append(f"IVR={iv}")
            if delta is not None:
                conditions.append(f"\u03b4={delta}")
            if dte is not None:
                conditions.append(f"DTE={dte}")
            if regime:
                conditions.append(f"regime={regime}")
            conditions_str = f" [{', '.join(conditions)}]" if conditions else ""

            # P&L
            pnl = e.get("pnl")
            pnl_str = ""
            if pnl is not None:
                sign = "+" if float(pnl) >= 0 else ""
                pnl_str = f" \u2192 P&L: {sign}${pnl}"

            lines.append(
                f"  {date_str}: {action} {contract} @ {price_str}"
                f"{conditions_str} ({status}){pnl_str}"
            )

        body = "\n".join(lines)
        return f"<recent_trades>\n{body}\n</recent_trades>"
