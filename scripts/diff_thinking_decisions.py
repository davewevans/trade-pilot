#!/usr/bin/env python3
"""Side-by-side comparison of adaptive thinking A/B test results.

Reads a .jsonl file produced by thinking_ab_test.py and prints a 3-column
comparison for every context where the decisions differed across modes.

Usage:
    python scripts/diff_thinking_decisions.py data/ab_tests/thinking_20260416T100000.jsonl
    python scripts/diff_thinking_decisions.py data/ab_tests/thinking_20260416T100000.jsonl --all
"""

import argparse
import json
import sys
from pathlib import Path


def _short_reasoning(reasoning_text: str | None, max_len: int = 80) -> str:
    """Collapse the reasoning JSON to a short summary string."""
    if not reasoning_text:
        return "—"
    try:
        r = json.loads(reasoning_text)
        if isinstance(r, dict):
            # Combine the most informative fields
            parts = []
            for key in ("selection", "volatility", "technical", "macro"):
                v = r.get(key, "")
                if v and v != "—":
                    parts.append(f"{key}: {v}")
            combined = " | ".join(parts)
            return combined[:max_len] + ("…" if len(combined) > max_len else "")
    except (json.JSONDecodeError, TypeError):
        pass
    s = str(reasoning_text)
    return s[:max_len] + ("…" if len(s) > max_len else "")


def _fmt_result(mode: str, r: dict) -> str:
    if "error" in r:
        return f"ERROR: {r['error']}"
    action   = r.get("action", "?")
    conf     = r.get("confidence") or "?"
    skip_why = r.get("skip_reason") or ""
    reason   = _short_reasoning(r.get("reasoning_text"))
    cost     = r.get("cost_usd", 0.0)
    lat      = r.get("latency_ms", 0)
    parts = [f"action={action} conf={conf} lat={lat}ms cost=${cost:.4f}"]
    if skip_why:
        parts.append(f"skip_reason={skip_why[:60]}")
    parts.append(reason)
    return "  " + "\n    ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Diff adaptive thinking A/B decisions")
    parser.add_argument("file", help="Path to .jsonl file from thinking_ab_test.py")
    parser.add_argument(
        "--all", action="store_true",
        help="Show all contexts, not just those where decisions differed",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    if not rows:
        print("No results found in file.")
        return

    modes = ["off", "adaptive_medium", "adaptive_high"]
    diffs = []
    same  = []

    for row in rows:
        actions = {m: row["results"].get(m, {}).get("action") for m in modes}
        unique = {v for v in actions.values() if v is not None}
        (diffs if len(unique) > 1 else same).append(row)

    to_show = rows if args.all else diffs

    print(f"\nFile: {path}")
    print(f"Total contexts: {len(rows)} | Differing: {len(diffs)} | Same: {len(same)}\n")

    if not to_show:
        print("No differing decisions found. All three modes agreed on every context.")
        if not args.all:
            print("Use --all to show all contexts regardless.")
        return

    header = "Differing decisions" if not args.all else "All decisions"
    print(f"{'='*70}")
    print(f"  {header} ({len(to_show)} of {len(rows)})")
    print(f"{'='*70}")

    for row in to_show:
        label = row.get("label", f"{row.get('strategy')}/{row.get('phase')}")
        ctx   = row.get("context_summary", {})
        print(f"\nContext: {label}")
        print(f"  regime={ctx.get('regime')} IVR={ctx.get('iv_rank')} env={ctx.get('iv_env')}")

        for m in modes:
            r = row["results"].get(m, {})
            label_str = {
                "off": "OFF            ",
                "adaptive_medium": "ADAPTIVE_MEDIUM",
                "adaptive_high":   "ADAPTIVE_HIGH  ",
            }[m]
            print(f"  {label_str}: {_fmt_result(m, r)}")

    print(f"\n{'='*70}")
    print(f"\nReview these diffs to judge whether thinking improves decision quality.")
    print(f"If decisions don't differ, adaptive thinking adds cost with no benefit.")


if __name__ == "__main__":
    main()
