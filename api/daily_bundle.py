"""Generate a markdown bundle with everything needed to evaluate one trading day.

Reads from:
- SQLite (decisions, trades, cycles, daily_summaries, token_usage)
- data/snapshots/*.json (portfolio, regime_history, circuit_breaker_state)
- data/snapshots/logs/YYYY-MM-DD.jsonl (structured warnings/errors from Prompt 7)

All reads are read-only. No side effects.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any


def build_daily_bundle(
    target_date: date,
    db_path: str,
    snapshots_dir: str,
    log_dir: str,
) -> str:
    """Build the markdown bundle for target_date. Returns the full string.

    Sections:
      1. Header
      2. Market context
      3. Cycle summary
      4. Claude agreement snapshot
      5. Decisions (full reasoning)
      6. Portfolio Greeks
      7. Data source health
      8. AI usage
      9. Errors & warnings summary
      10. Cycle timing
    """
    lines: list[str] = [
        _section_header(target_date, db_path, snapshots_dir),
        _section_market_context(target_date, db_path, snapshots_dir),
        _section_cycle_summary(target_date, db_path),
        _section_claude_agreement(target_date, db_path),
        _section_decisions(target_date, db_path),
        _section_portfolio_greeks(snapshots_dir),
        _section_data_health(snapshots_dir),
        _section_ai_usage(target_date, db_path),
        _section_errors_warnings(target_date, log_dir),
        _section_cycle_timing(target_date, log_dir),
    ]
    return "\n\n---\n\n".join(lines)


# ── helpers ──────────────────────────────────────────────────


def _db_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _date_prefix(d: date) -> str:
    return d.isoformat()


# ── sections ─────────────────────────────────────────────────


def _section_header(target_date: date, db_path: str, snapshots_dir: str) -> str:
    snap = Path(snapshots_dir)

    # Version
    try:
        from version import VERSION, VERSION_DATE
        version_str = f"{VERSION} ({VERSION_DATE})"
    except Exception:
        version_str = "unknown"

    # Circuit breaker
    cb = _read_json(snap / "circuit_breaker_state.json") or {}
    cb_status = cb.get("state", "unknown")

    # Open positions count by strategy from portfolio snapshots
    pos_lines: list[str] = []
    for pf in sorted(snap.glob("portfolio_*.json")):
        pf_data = _read_json(pf) or {}
        positions = pf_data.get("positions", [])
        acct = pf.stem.replace("portfolio_", "")
        pos_lines.append(f"  - {acct}: {len(positions)} open position(s)")
    if not pos_lines:
        pos_lines = ["  - _no portfolio snapshots found_"]

    return "\n".join([
        f"## trade-pilot Daily Bundle — {target_date.isoformat()}",
        f"",
        f"**Bot version:** {version_str}",
        f"**Circuit breaker:** {cb_status}",
        f"**Open positions by account:**",
        *pos_lines,
    ])


def _section_market_context(target_date: date, db_path: str, snapshots_dir: str) -> str:
    snap = Path(snapshots_dir)
    parts = ["## Market context"]

    # regime_history.json
    rh = _read_json(snap / "regime_history.json")
    if rh and isinstance(rh, list) and rh:
        latest = rh[-1]
        parts.append(f"- **Regime:** {latest.get('regime', 'n/a')}")
        parts.append(f"- **VIX:** {latest.get('vix', 'n/a')}")
        parts.append(f"- **Fear & Greed:** {latest.get('fear_greed_value', 'n/a')} ({latest.get('fear_greed_rating', 'n/a')})")
        parts.append(f"- **IV environment:** {latest.get('iv_environment', 'n/a')}")
        parts.append(f"- **Stability:** {latest.get('stability', 'n/a')}")
        parts.append(f"- **Recorded at:** {latest.get('timestamp', 'n/a')}")
    else:
        parts.append("_No regime history snapshot found._")

    # daily_summaries row for target_date
    try:
        with _db_conn(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM daily_summaries WHERE date = ?",
                (target_date.isoformat(),),
            ).fetchone()
        if row:
            parts.append(f"\n**Daily summary:** decisions={row['decisions_total']}, "
                         f"trades={row['trades_executed']}, "
                         f"skips={row['skips']}, "
                         f"premium=${row['premium_collected']:.2f}")
        else:
            parts.append("\n_No daily_summaries row for this date._")
    except Exception:
        parts.append("\n_Could not read daily_summaries table._")

    return "\n".join(parts)


def _section_cycle_summary(target_date: date, db_path: str) -> str:
    parts = ["## Cycle summary"]
    date_str = target_date.isoformat()
    try:
        with _db_conn(db_path) as conn:
            rows = conn.execute(
                """
                SELECT strategy_type, underlying, opened_at, closed_at, status, outcome, total_premium
                FROM cycles
                WHERE DATE(opened_at) = ?
                ORDER BY opened_at
                """,
                (date_str,),
            ).fetchall()
        if not rows:
            parts.append("_No cycles found for this date._")
        else:
            parts.append(f"| Strategy | Underlying | Opened | Closed | Status | Outcome | Premium |")
            parts.append(f"|----------|-----------|--------|--------|--------|---------|---------|")
            for r in rows:
                premium = f"${r['total_premium']:.2f}" if r["total_premium"] is not None else "—"
                parts.append(
                    f"| {r['strategy_type']} | {r['underlying']} "
                    f"| {(r['opened_at'] or '')[:19]} | {(r['closed_at'] or '—')[:19]} "
                    f"| {r['status']} | {r['outcome'] or '—'} | {premium} |"
                )
    except Exception:
        parts.append("_Could not read cycles table._")
    return "\n".join(parts)


def _section_claude_agreement(target_date: date, db_path: str) -> str:
    parts = ["## Claude agreement snapshot"]
    date_str = target_date.isoformat()
    try:
        with _db_conn(db_path) as conn:
            rows = conn.execute(
                "SELECT action, COUNT(*) as cnt FROM decisions WHERE DATE(timestamp) = ? GROUP BY action",
                (date_str,),
            ).fetchall()
        if not rows:
            parts.append("_No decisions found for this date._")
            return "\n".join(parts)
        counts = {r["action"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        open_count = counts.get("OPEN", 0)
        skip_count = counts.get("SKIP", 0)
        other = total - open_count - skip_count
        parts.append(f"- **Total decisions:** {total}")
        parts.append(f"- **OPEN:** {open_count} ({open_count/total*100:.0f}%)")
        parts.append(f"- **SKIP:** {skip_count} ({skip_count/total*100:.0f}%)")
        if other:
            parts.append(f"- **Other:** {other}")
    except Exception:
        parts.append("_Could not read decisions table._")
    return "\n".join(parts)


def _section_decisions(target_date: date, db_path: str) -> str:
    parts = ["## Decisions"]
    date_str = target_date.isoformat()
    try:
        with _db_conn(db_path) as conn:
            rows = conn.execute(
                """
                SELECT timestamp, strategy_type, underlying, action, confidence, reasoning
                FROM decisions
                WHERE DATE(timestamp) = ?
                ORDER BY timestamp
                """,
                (date_str,),
            ).fetchall()
        if not rows:
            parts.append("_No decisions recorded for this date._")
            return "\n".join(parts)
        for r in rows:
            ts_short = (r["timestamp"] or "")[:19]
            conf = r["confidence"]
            conf_str = f"{conf:.0%}" if conf is not None else "n/a"
            parts.append(f"### {r['action']} · {r['underlying']} · {r['strategy_type']} · {ts_short}")
            parts.append(f"**Confidence:** {conf_str}")
            reasoning = r["reasoning"]
            if reasoning:
                try:
                    rdata = json.loads(reasoning)
                    if isinstance(rdata, dict):
                        for section, text in rdata.items():
                            parts.append(f"**{section}.** {text}")
                    else:
                        parts.append(str(rdata))
                except (json.JSONDecodeError, TypeError):
                    parts.append(str(reasoning))
            parts.append("")
    except Exception:
        parts.append("_Could not read decisions table._")
    return "\n".join(parts)


def _section_portfolio_greeks(snapshots_dir: str) -> str:
    parts = ["## Portfolio Greeks"]
    snap = Path(snapshots_dir)
    pf = _read_json(snap / "portfolio.json") or {}
    greeks = pf.get("greeks") or pf.get("portfolio_greeks") or {}
    if greeks:
        parts.append(f"- **Net delta:** {greeks.get('delta', 'n/a')}")
        parts.append(f"- **Net theta:** {greeks.get('theta', 'n/a')}")
        parts.append(f"- **Net vega:** {greeks.get('vega', 'n/a')}")
        parts.append(f"- **Net gamma:** {greeks.get('gamma', 'n/a')}")
    else:
        parts.append("_No Greeks data in portfolio snapshot._")
    return "\n".join(parts)


def _section_data_health(snapshots_dir: str) -> str:
    parts = ["## Data source health"]
    snap = Path(snapshots_dir)
    sh = _read_json(snap / "source_health.json")
    if sh and isinstance(sh, dict):
        parts.append("| Source | Status | Last success |")
        parts.append("|--------|--------|-------------|")
        for source, info in sh.items():
            if isinstance(info, dict):
                status = "✓" if info.get("healthy") else "✗"
                last_ok = info.get("last_success") or info.get("last_check") or "n/a"
                parts.append(f"| {source} | {status} | {str(last_ok)[:19]} |")
            else:
                parts.append(f"| {source} | {info} | — |")
    else:
        parts.append("_No source_health.json snapshot found._")
    return "\n".join(parts)


def _section_ai_usage(target_date: date, db_path: str) -> str:
    parts = ["## AI usage"]
    date_str = target_date.isoformat()
    try:
        with _db_conn(db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    strategy_type,
                    COUNT(*) as calls,
                    SUM(input_tokens) as input_tok,
                    SUM(output_tokens) as output_tok,
                    SUM(cache_read_tokens) as cache_read,
                    SUM(cache_creation_tokens) as cache_write,
                    SUM(estimated_cost_usd) as cost_usd
                FROM token_usage
                WHERE DATE(timestamp) = ?
                GROUP BY strategy_type
                ORDER BY cost_usd DESC
                """,
                (date_str,),
            ).fetchall()
        if not rows:
            parts.append("_No token_usage data for this date._")
        else:
            parts.append("| Strategy | Calls | Input tok | Output tok | Cache read | Cost |")
            parts.append("|----------|-------|-----------|------------|------------|------|")
            total_cost = 0.0
            for r in rows:
                cost = r["cost_usd"] or 0.0
                total_cost += cost
                parts.append(
                    f"| {r['strategy_type'] or '—'} | {r['calls']} "
                    f"| {r['input_tok'] or 0:,} | {r['output_tok'] or 0:,} "
                    f"| {r['cache_read'] or 0:,} | ${cost:.4f} |"
                )
            parts.append(f"\n**Total estimated cost:** ${total_cost:.4f}")
    except Exception:
        parts.append("_Could not read token_usage table._")
    return "\n".join(parts)


def _section_errors_warnings(target_date: date, log_dir: str) -> str:
    path = Path(log_dir) / f"{target_date.isoformat()}.jsonl"
    if not path.exists():
        return "## Errors & warnings\n\n_No structured log file found for this date._"
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            rec = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        key = (rec.get("logger", "?"), rec.get("message", "?").split("\n", 1)[0][:120])
        groups[key].append(rec)
    if not groups:
        return "## Errors & warnings\n\n_Log file exists but is empty._"
    sorted_groups = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    parts = ["## Errors & warnings"]
    for (logger_name, message_head), recs in sorted_groups:
        first = recs[0]
        parts.append(f"### `{logger_name}` · {message_head}")
        parts.append(f"- **Count:** {len(recs)}")
        parts.append(f"- **First occurrence:** {first.get('ts')}")
        if first.get("extra"):
            parts.append(f"- **First extra:** `{json.dumps(first['extra'], default=str)[:300]}`")
        if first.get("traceback"):
            parts.append("- **First traceback:**")
            parts.append("```")
            parts.append(first["traceback"].strip())
            parts.append("```")
        parts.append("")
    return "\n".join(parts)


def _section_cycle_timing(target_date: date, log_dir: str) -> str:
    parts = ["## Cycle timing"]
    path = Path(log_dir) / f"{target_date.isoformat()}.jsonl"
    if not path.exists():
        parts.append("_No structured log file — timing data unavailable._")
        return "\n".join(parts)

    context_build_times: list[dict] = []
    completed_jobs: list[dict] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            rec = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message", "")
        extra = rec.get("extra", {}) or {}
        if "context_build" in msg and extra.get("elapsed_s") is not None:
            context_build_times.append({"symbol": extra.get("symbol", "?"), "elapsed_s": extra["elapsed_s"], "ts": rec.get("ts", "")})
        if "COMPLETED" in msg and "market_open" in msg.lower():
            completed_jobs.append({"ts": rec.get("ts", ""), "message": msg})

    if context_build_times:
        context_build_times.sort(key=lambda r: r.get("elapsed_s", 0), reverse=True)
        parts.append("**Top context build times (slowest first):**")
        for cb in context_build_times[:10]:
            parts.append(f"- {cb['symbol']}: {cb['elapsed_s']:.1f}s")
    else:
        parts.append("_No context build timing records found in log._")

    if completed_jobs:
        parts.append(f"\n**market_open completed:** {completed_jobs[-1]['ts'][:19]}")

    return "\n".join(parts)
