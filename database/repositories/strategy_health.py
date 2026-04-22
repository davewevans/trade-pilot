"""Repository for per-strategy per-ISO-week decision funnel counts.

Placed in a dedicated file rather than added to decisions.py because:
1. The query joins decisions AND trades — it spans two tables.
2. The aggregation logic is substantial; decisions.py is already large.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
import sqlite3


# Every SkipGate enum value mapped to its funnel bucket name.
# If SkipGate gains a new value, add it here — test_all_skipgate_enum_values_have_a_bucket
# will catch the omission.
# All strategy types the bot can produce. Used to pre-seed the strategy list
# so the health page dropdown always shows every strategy, even ones with no
# activity in the current window.
KNOWN_STRATEGY_TYPES: tuple[str, ...] = (
    "wheel_csp",
    "wheel_cc",
    "turnover_wheel_csp",
    "turnover_wheel_cc",
    "iron_condor",
    "bull_put_spread",
    "bear_call_spread",
    "long_call_vertical",
    "iron_butterfly",
    "calendar_spread",
)

_GATE_TO_BUCKET: dict[str, str] = {
    "pre_check":       "skip_pre_check",
    "claude_skip":     "skip_claude",
    "llm_output":      "skip_llm_output",
    "guardrail":       "skip_guardrail",
    "circuit_breaker": "skip_circuit_breaker",
    "macro_event":     "skip_macro_event",
    "liquidity_floor": "skip_liquidity_floor",
    "winrate_floor":   "skip_winrate_floor",
    "no_candidate":    "skip_no_candidate",
    "data_missing":    "skip_data_missing",
    "halted":          "skip_halted",
    "portfolio":       "skip_portfolio",
}

_DECISION_BUCKETS = (
    "decisions_total",
    "skip_pre_check",
    "skip_claude",
    "skip_llm_output",
    "skip_guardrail",
    "skip_circuit_breaker",
    "skip_macro_event",
    "skip_liquidity_floor",
    "skip_winrate_floor",
    "skip_no_candidate",
    "skip_data_missing",
    "skip_halted",
    "skip_portfolio",
    "skip_unclassified",
    "hold",
    "actions_proposed",
)

_TRADE_BUCKETS = (
    "trades_submitted",
    "trades_filled",
    "trades_pending",
    "trades_closed_profit",
    "trades_closed_loss",
    "trades_closed_breakeven",
    "trades_closed_unknown",
)


def _monday_of_week(dt: datetime) -> datetime:
    """Return Monday 00:00:00 UTC of the ISO week containing dt."""
    days_back = dt.weekday()  # 0=Monday, 6=Sunday
    monday = dt - timedelta(days=days_back)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


class StrategyHealthRepository:
    """Read-only aggregation across decisions and trades for funnel observability."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get_strategy_health_funnel(
        self,
        weeks_back: int = 12,
        strategy_types: list[str] | None = None,
    ) -> list[dict]:
        """Return per-strategy per-ISO-week funnel rows.

        weeks_back: total number of ISO weeks to include, counting the
                    current partial week as 1.  weeks_back=1 → current
                    partial week only; weeks_back=12 → current week plus
                    11 prior complete weeks.
        strategy_types: optional allowlist; None returns all strategies.

        Each returned dict contains:
            strategy_type, iso_week (YYYY-WW), week_start_date (ISO date),
            all funnel count fields from _DECISION_BUCKETS + _TRADE_BUCKETS,
            top_claude_skip_reason (str | None),
            top_guardrail_reason (str | None, truncated to 50 chars).

        Strategies silent in a given week appear with all counts = 0.
        Ordering: most recent week first, then alphabetical strategy_type.
        """
        now = datetime.now(timezone.utc)
        current_week_monday = _monday_of_week(now)
        range_start = current_week_monday - timedelta(weeks=weeks_back - 1)

        # Build the canonical iso_week → week_start_date mapping for every
        # week we want to return. Weeks outside this mapping are ignored.
        week_map: dict[str, str] = {}
        w = range_start
        while w <= current_week_monday:
            week_map[w.strftime("%Y-%W")] = w.date().isoformat()
            w += timedelta(weeks=1)

        # SQLite timestamp comparison: decisions/trades store naive UTC ISO strings.
        cutoff_str = range_start.strftime("%Y-%m-%dT%H:%M:%S")

        # Optional strategy filter clause (same form for all three queries).
        extra_clause = ""
        extra_params: list = []
        if strategy_types:
            placeholders = ",".join(["?"] * len(strategy_types))
            extra_clause = f" AND strategy_type IN ({placeholders})"
            extra_params = list(strategy_types)

        # ── Query 1: Decision counts ───────────────────────────────────────
        # Group by the fields that fully determine bucket assignment so we
        # minimise data transfer; Python does the final bucketing.
        dec_rows = self._conn.execute(
            f"""
            SELECT strategy_type,
                   strftime('%Y-%W', timestamp)   AS iso_week,
                   action,
                   skip_gate,
                   skip_reason_code,
                   COUNT(*)                        AS cnt
              FROM decisions
             WHERE timestamp >= ?{extra_clause}
             GROUP BY strategy_type, iso_week, action, skip_gate, skip_reason_code
            """,
            [cutoff_str, *extra_params],
        ).fetchall()

        # ── Query 2: Trade counts ─────────────────────────────────────────
        # Use submitted_at for week attribution (matches the design spec).
        trade_rows = self._conn.execute(
            f"""
            SELECT strategy_type,
                   strftime('%Y-%W', submitted_at)                          AS iso_week,
                   CASE WHEN fill_price IS NOT NULL THEN 1 ELSE 0 END       AS has_fill,
                   CASE WHEN fill_price IS NULL AND outcome IS NULL THEN 1
                        ELSE 0 END                                          AS is_pending,
                   outcome,
                   COUNT(*)                                                 AS cnt
              FROM trades
             WHERE submitted_at >= ?{extra_clause}
             GROUP BY strategy_type, iso_week, has_fill, is_pending, outcome
            """,
            [cutoff_str, *extra_params],
        ).fetchall()

        # ── Query 3: Guardrail reasoning text ────────────────────────────
        # We want the most common 50-char prefix of the raw reasoning string
        # per (strategy, week). Doing the truncation in SQL avoids pulling
        # full reasoning blobs into Python.
        guardrail_rows = self._conn.execute(
            f"""
            SELECT strategy_type,
                   strftime('%Y-%W', timestamp)          AS iso_week,
                   SUBSTR(COALESCE(reasoning, ''), 1, 50) AS reason_prefix,
                   COUNT(*)                               AS cnt
              FROM decisions
             WHERE timestamp >= ?
               AND skip_gate = 'guardrail'{extra_clause}
             GROUP BY strategy_type, iso_week, reason_prefix
            """,
            [cutoff_str, *extra_params],
        ).fetchall()

        # ── Python aggregation ─────────────────────────────────────────────
        # Pre-seed with all known strategies so the health page dropdown always
        # shows every strategy, even ones with no activity in this window.
        # When the caller passes a filter, only seed the filtered subset.
        seed = set(strategy_types) if strategy_types else set(KNOWN_STRATEGY_TYPES)
        all_strategies: set[str] = seed

        # (strategy, iso_week) → {bucket_name: count}
        dec_data: dict[tuple, dict] = {}
        # (strategy, iso_week) → Counter of skip_reason_code
        claude_counters: dict[tuple, Counter] = {}

        for row in dec_rows:
            st, iw, action, skip_gate, skip_reason_code, cnt = (
                row[0], row[1], row[2], row[3], row[4], int(row[5])
            )
            if iw not in week_map:
                continue
            all_strategies.add(st)
            key = (st, iw)
            if key not in dec_data:
                dec_data[key] = {b: 0 for b in _DECISION_BUCKETS}
            d = dec_data[key]
            d["decisions_total"] += cnt

            action_up = (action or "").upper()
            if action_up == "SKIP":
                gate = skip_gate or ""
                bucket = _GATE_TO_BUCKET.get(gate, "skip_unclassified")
                d[bucket] += cnt
                if gate == "claude_skip" and skip_reason_code:
                    if key not in claude_counters:
                        claude_counters[key] = Counter()
                    claude_counters[key][skip_reason_code] += cnt
            elif action_up == "HOLD":
                d["hold"] += cnt
            else:
                d["actions_proposed"] += cnt

        trade_data: dict[tuple, dict] = {}

        for row in trade_rows:
            st, iw, has_fill, is_pending, outcome, cnt = (
                row[0], row[1], int(row[2]), int(row[3]), row[4], int(row[5])
            )
            if iw not in week_map:
                continue
            all_strategies.add(st)
            key = (st, iw)
            if key not in trade_data:
                trade_data[key] = {b: 0 for b in _TRADE_BUCKETS}
            t = trade_data[key]
            t["trades_submitted"] += cnt
            if has_fill:
                t["trades_filled"] += cnt
            if is_pending:
                t["trades_pending"] += cnt
            if outcome == "profit":
                t["trades_closed_profit"] += cnt
            elif outcome == "loss":
                t["trades_closed_loss"] += cnt
            elif outcome == "breakeven":
                t["trades_closed_breakeven"] += cnt
            elif outcome == "unknown":
                t["trades_closed_unknown"] += cnt

        guardrail_counters: dict[tuple, Counter] = {}
        for row in guardrail_rows:
            st, iw, reason_prefix, cnt = row[0], row[1], row[2], int(row[3])
            if iw not in week_map:
                continue
            all_strategies.add(st)
            key = (st, iw)
            if key not in guardrail_counters:
                guardrail_counters[key] = Counter()
            guardrail_counters[key][reason_prefix or ""] += cnt

        # ── Build final rows ───────────────────────────────────────────────
        # Every (strategy, week) combination in the range appears, with zeros
        # where there was no activity. Sort: most recent week first, then
        # alphabetical strategy_type.
        _zero_dec = {b: 0 for b in _DECISION_BUCKETS}
        _zero_trade = {b: 0 for b in _TRADE_BUCKETS}

        results: list[dict] = []
        for iw in sorted(week_map.keys(), reverse=True):
            for st in sorted(all_strategies):
                key = (st, iw)
                d = dec_data.get(key, _zero_dec)
                t = trade_data.get(key, _zero_trade)

                cc = claude_counters.get(key)
                gc = guardrail_counters.get(key)

                results.append({
                    "strategy_type": st,
                    "iso_week": iw,
                    "week_start_date": week_map[iw],
                    # decision buckets
                    "decisions_total": d["decisions_total"],
                    "skip_pre_check": d["skip_pre_check"],
                    "skip_claude": d["skip_claude"],
                    "skip_guardrail": d["skip_guardrail"],
                    "skip_circuit_breaker": d["skip_circuit_breaker"],
                    "skip_liquidity_floor": d["skip_liquidity_floor"],
                    "skip_winrate_floor": d["skip_winrate_floor"],
                    "skip_no_candidate": d["skip_no_candidate"],
                    "skip_data_missing": d["skip_data_missing"],
                    "skip_halted": d["skip_halted"],
                    "skip_unclassified": d["skip_unclassified"],
                    "hold": d["hold"],
                    "actions_proposed": d["actions_proposed"],
                    # trade buckets
                    "trades_submitted": t["trades_submitted"],
                    "trades_filled": t["trades_filled"],
                    "trades_pending": t["trades_pending"],
                    "trades_closed_profit": t["trades_closed_profit"],
                    "trades_closed_loss": t["trades_closed_loss"],
                    "trades_closed_breakeven": t["trades_closed_breakeven"],
                    "trades_closed_unknown": t["trades_closed_unknown"],
                    # computed
                    "top_claude_skip_reason": cc.most_common(1)[0][0] if cc else None,
                    "top_guardrail_reason": (gc.most_common(1)[0][0] or None) if gc else None,
                })

        return results
