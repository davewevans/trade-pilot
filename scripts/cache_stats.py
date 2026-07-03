#!/usr/bin/env python3
"""Print a cache-hit-rate summary from the claude_api_calls table.

Usage:
    python scripts/cache_stats.py            # last 7 days
    python scripts/cache_stats.py --days 14  # last 14 days

Pricing is sourced from config.CLAUDE_PRICING for the currently configured
settings.ADVISOR_MODEL, so this report always reflects the live pricing
table instead of a hardcoded snapshot. If the model isn't in CLAUDE_PRICING,
falls back to the claude-sonnet-4-6 rates and prints a warning.
"""

import argparse
import sys
from pathlib import Path

# Allow running from the project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as _config  # noqa: E402 — must come after sys.path insert
from config import settings  # noqa: E402
from database.db import Database  # noqa: E402

# ── Pricing (per million tokens) ─────────────────────────────────────────────
_FALLBACK_MODEL = "claude-sonnet-4-6"
try:
    _pricing = _config.get_pricing(settings.ADVISOR_MODEL)
except KeyError:
    print(
        f"WARNING: no CLAUDE_PRICING entry for model {settings.ADVISOR_MODEL!r} — "
        f"falling back to {_FALLBACK_MODEL!r} rates. Report cost figures may be wrong."
    )
    _pricing = _config.get_pricing(_FALLBACK_MODEL)

_PRICE_INPUT_PER_M       = _pricing["input_per_mtok"]
_PRICE_CACHE_WRITE_PER_M = _pricing["cache_write_per_mtok"]
_PRICE_CACHE_READ_PER_M  = _pricing["cache_read_per_mtok"]
_PRICE_OUTPUT_PER_M      = _pricing["output_per_mtok"]


def _cost(input_tok: int, cache_read: int, cache_write: int, output_tok: int) -> float:
    """Return estimated $ cost for a set of token counts."""
    return (
        input_tok    / 1_000_000 * _PRICE_INPUT_PER_M
        + cache_write / 1_000_000 * _PRICE_CACHE_WRITE_PER_M
        + cache_read  / 1_000_000 * _PRICE_CACHE_READ_PER_M
        + output_tok  / 1_000_000 * _PRICE_OUTPUT_PER_M
    )


def _hit_pct(cache_read: int, cache_write: int, input_tok: int) -> float:
    total = cache_read + cache_write + input_tok
    return round(cache_read / total * 100, 1) if total else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude API cache hit rate report")
    parser.add_argument("--days", type=int, default=7, help="Look-back window in days (default: 7)")
    args = parser.parse_args()

    db = Database()
    db.init_schema()
    conn = db.get_connection()

    rows = conn.execute(
        """
        SELECT * FROM claude_api_calls
        WHERE created_at >= datetime('now', ? || ' days')
        ORDER BY created_at ASC
        """,
        (f"-{args.days}",),
    ).fetchall()

    if not rows:
        print(f"No API call records in the last {args.days} days.")
        return

    rows = [dict(r) for r in rows]

    # ── Aggregate totals ─────────────────────────────────────────────────────
    total_calls   = len(rows)
    total_input   = sum(r["input_tokens"] for r in rows)
    total_cr      = sum(r["cache_read_tokens"] for r in rows)
    total_cw      = sum(r["cache_write_tokens"] for r in rows)
    total_output  = sum(r["output_tokens"] for r in rows)
    total_lat_ms  = sum(r["latency_ms"] for r in rows)

    avg_hit = _hit_pct(total_cr, total_cw, total_input)
    total_cost = _cost(total_input, total_cr, total_cw, total_output)
    cost_if_uncached = _cost(total_input + total_cr, 0, 0, total_output)
    savings = cost_if_uncached - total_cost

    print(f"\n{'='*60}")
    print(f"  Claude API Cache Stats — last {args.days} day(s)")
    print(f"{'='*60}")
    print(f"  Total calls       : {total_calls:,}")
    print(f"  Avg latency       : {total_lat_ms / total_calls:.0f} ms")
    print(f"  Input tokens      : {total_input:,}")
    print(f"  Cache-write tokens: {total_cw:,}")
    print(f"  Cache-read tokens : {total_cr:,}")
    print(f"  Output tokens     : {total_output:,}")
    print(f"  Avg cache hit     : {avg_hit}%")
    print(f"  Est. total cost   : ${total_cost:.4f}")
    print(f"  Cost if uncached  : ${cost_if_uncached:.4f}")
    print(f"  Est. savings      : ${savings:.4f}")

    # ── By hour of day ───────────────────────────────────────────────────────
    from collections import defaultdict
    hour_buckets: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        try:
            # created_at is ISO: "2025-04-16T14:23:01+00:00" or "…Z" or bare UTC
            ts = r["created_at"]
            hour = int(ts[11:13])
        except (TypeError, IndexError, ValueError):
            hour = -1
        hour_buckets[hour].append(r)

    print(f"\n  Cache hit % by hour of day (UTC):")
    print(f"  {'Hour':>5}  {'Calls':>6}  {'Hit%':>6}  {'Est Cost':>9}")
    print(f"  {'-'*35}")
    for hour in sorted(hour_buckets):
        bucket = hour_buckets[hour]
        b_cr  = sum(r["cache_read_tokens"] for r in bucket)
        b_cw  = sum(r["cache_write_tokens"] for r in bucket)
        b_in  = sum(r["input_tokens"] for r in bucket)
        b_out = sum(r["output_tokens"] for r in bucket)
        hp    = _hit_pct(b_cr, b_cw, b_in)
        co    = _cost(b_in, b_cr, b_cw, b_out)
        label = f"{hour:02d}:00" if hour >= 0 else " N/A "
        print(f"  {label:>5}  {len(bucket):>6}  {hp:>5.1f}%  ${co:>8.4f}")

    # ── Cost breakdown ───────────────────────────────────────────────────────
    cost_input  = total_input   / 1_000_000 * _PRICE_INPUT_PER_M
    cost_cw     = total_cw      / 1_000_000 * _PRICE_CACHE_WRITE_PER_M
    cost_cr     = total_cr      / 1_000_000 * _PRICE_CACHE_READ_PER_M
    cost_output = total_output  / 1_000_000 * _PRICE_OUTPUT_PER_M
    print(f"\n  Cost breakdown:")
    print(f"    Uncached input : ${cost_input:.4f}")
    print(f"    Cache writes   : ${cost_cw:.4f}")
    print(f"    Cache reads    : ${cost_cr:.4f}")
    print(f"    Output tokens  : ${cost_output:.4f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
