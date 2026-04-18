"""Monthly evaluation job.

Runs on the 1st of each month at 05:00 ET (the scheduler wrapper enforces
the date gate; this module does not check it).

Pipeline for a calendar month M:
  1. Programmatic scorer over M's decision window.
  2. Judge scorer over M (if EVALUATION_JUDGE_ENABLED).
  3. Aggregate via evaluation.aggregator.
  4. Load prior 3-month aggregates for trend detection.
  5. Run flag detector.
  6. Mark a random 10-20% sample of score rows spot_check_pending=True.
  7. Query prior month's judge-operator disagreement rate.
  8. UPSERT monthly_evaluations row.
  9. Write markdown archive to data/reports/monthly_eval_YYYY-MM.md.
  10. Fire ntfy notification (warning severity) ONLY when flag count > 0.

Entry points:
  - run(month=None): called by scheduler and by main.py --job=monthly_evaluation.
    ``month`` is YYYY-MM; defaults to the prior calendar month.
"""

from __future__ import annotations

import calendar
import json
import logging
import random
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from database.repositories.decision_scores_repository import DecisionScoresRepository
    from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

# Fraction of score rows to mark for operator spot-check (midpoint of 10–20%).
_SPOT_CHECK_FRACTION = 0.15


# ── Date helpers ─────────────────────────────────────────────────────────────


def _prior_calendar_month(today: date) -> str:
    """Return YYYY-MM for the calendar month immediately before *today*."""
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def _month_window(month: str) -> tuple[str, str]:
    """Return (start_iso, end_iso) spanning all of *month* (YYYY-MM)."""
    year, mon = int(month[:4]), int(month[5:7])
    last_day = calendar.monthrange(year, mon)[1]
    return f"{month}-01T00:00:00", f"{month}-{last_day:02d}T23:59:59"


def _decrement_month(month: str) -> str:
    """Return the YYYY-MM that immediately precedes *month*."""
    year, mon = int(month[:4]), int(month[5:7])
    mon -= 1
    if mon == 0:
        mon = 12
        year -= 1
    return f"{year}-{mon:02d}"


def _prior_n_months(month: str, n: int) -> list[str]:
    """Return a list of the *n* months before *month*, oldest-first."""
    result: list[str] = []
    m = month
    for _ in range(n):
        m = _decrement_month(m)
        result.append(m)
    return list(reversed(result))


# ── Pipeline helpers ─────────────────────────────────────────────────────────


def _load_prior_aggregates(
    month: str,
    scores_repo: "DecisionScoresRepository",
    evals_repo: "MonthlyEvaluationsRepository",
    n: int = 3,
) -> list[dict]:
    """Return up to *n* prior monthly aggregate dicts (oldest-first).

    Strategy: load from monthly_evaluations.score_distribution_json when
    available (fast); otherwise re-aggregate from decision_scores rows.
    """
    from evaluation.aggregator import aggregate_scores

    prior_months = _prior_n_months(month, n)
    history: list[dict] = []
    for m in prior_months:
        row = evals_repo.get_by_month(m)
        if row and row.get("score_distribution_json"):
            try:
                dist = json.loads(row["score_distribution_json"])
                history.append(
                    {
                        "month": m,
                        "by_strategy": dist.get("by_strategy", {}),
                        "overall": dist.get("overall", {}),
                    }
                )
                continue
            except (json.JSONDecodeError, TypeError):
                pass
        # Fallback: re-aggregate from raw score rows
        agg = aggregate_scores(m, scores_repo)
        if agg.get("by_strategy"):
            history.append(agg)
    return history


def _mark_spot_check_sample(month: str, scores_repo: "DecisionScoresRepository") -> int:
    """Set spot_check_pending=True on a random 10–20% sample of rows for *month*.

    Returns the number of rows marked.
    """
    rows = scores_repo.get_by_month(month)
    if not rows:
        return 0
    sample_size = max(1, round(len(rows) * _SPOT_CHECK_FRACTION))
    sample = random.sample(rows, min(sample_size, len(rows)))
    ids = [r["id"] for r in sample]
    scores_repo.mark_for_spot_check(ids)
    return len(ids)


def _compute_avg_and_pct_pass(score_rows: list[dict]) -> tuple[float | None, float | None]:
    """Return (avg_score_0_to_100, pct_pass_0_to_1) from raw score rows."""
    if not score_rows:
        return None, None
    total_scores = [
        float(r["total_score"])
        for r in score_rows
        if r.get("total_score") is not None
    ]
    avg_score = sum(total_scores) / len(total_scores) if total_scores else None
    pass_count = sum(1 for r in score_rows if r.get("pass_fail") == "pass")
    pct_pass = pass_count / len(score_rows)
    return avg_score, pct_pass


# ── Markdown archive ─────────────────────────────────────────────────────────


def _write_markdown_archive(
    *,
    month: str,
    aggregate: dict,
    flags: list[dict],
    has_insufficient_sample: bool,
    decisions_scored: int,
    closed_trades: int,
    disagreement_rate: float | None,
    created_at: str,
    reports_dir: Path,
) -> Path:
    """Write data/reports/monthly_eval_YYYY-MM.md and return the path."""
    lines: list[str] = []

    # ── Title ──────────────────────────────────────────────────────────────
    lines.append(f"# Monthly Evaluation: {month}")
    lines.append(f"_Generated: {created_at}_")
    lines.append("")

    # ── Summary ────────────────────────────────────────────────────────────
    prior_month = _decrement_month(month)
    disagreement_str = (
        f"{disagreement_rate * 100:.1f}%" if disagreement_rate is not None else "n/a"
    )
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Decisions scored | {decisions_scored} |")
    lines.append(f"| Closed trades in window | {closed_trades} |")
    lines.append(f"| Insufficient sample | {str(has_insufficient_sample).lower()} |")
    lines.append(f"| Flags | {len(flags)} |")
    lines.append(
        f"| Judge-operator disagreement ({prior_month}) | {disagreement_str} |"
    )
    lines.append("")

    # ── Flagged Dimensions ─────────────────────────────────────────────────
    if flags:
        lines.append("## Flagged Dimensions")
        lines.append("")
        for flag in flags:
            strategy = flag.get("strategy", "unknown")
            dimension = flag.get("dimension", "unknown")
            lines.append(f"### {strategy} / {dimension}")
            lines.append("")
            lines.append(f"- **Severity:** {flag.get('severity', 'warning')}")
            lines.append(f"- **Reason:** {flag.get('reason', '')}")
            lowest = flag.get("lowest_scoring_decisions", [])
            if lowest:
                ids_str = ", ".join(str(d) for d in lowest)
                lines.append(f"- **Lowest-scoring decision IDs:** {ids_str}")
            lines.append("")

    # ── Score Distribution ─────────────────────────────────────────────────
    by_strategy: dict = aggregate.get("by_strategy", {})
    if by_strategy:
        lines.append("## Score Distribution")
        lines.append("")
        for strategy, strategy_data in sorted(by_strategy.items()):
            lines.append(f"### Strategy: {strategy}")
            lines.append("")
            lines.append(
                f"_Decisions scored: {strategy_data.get('decisions_scored', 0)} | "
                f"Closed trades: {strategy_data.get('closed_trades_in_window', 0)}_"
            )
            lines.append("")
            by_dim: dict = strategy_data.get("by_dimension", {})
            if by_dim:
                lines.append("| Dimension | Mean | Median | Stddev | N |")
                lines.append("|-----------|------|--------|--------|---|")
                for dim, stats in sorted(by_dim.items()):
                    lines.append(
                        f"| {dim} "
                        f"| {stats.get('mean', 0.0):.4f} "
                        f"| {stats.get('median', 0.0):.4f} "
                        f"| {stats.get('stddev', 0.0):.4f} "
                        f"| {stats.get('n', 0)} |"
                    )
                lines.append("")
            else:
                lines.append("_No dimension scores available._")
                lines.append("")

    content = "\n".join(lines)
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"monthly_eval_{month}.md"
    out_path.write_text(content, encoding="utf-8")
    logger.info("monthly_evaluation: markdown archive written to %s", out_path)
    return out_path


# ── Core pipeline ─────────────────────────────────────────────────────────────


def _run_pipeline(month: str, start_iso: str, end_iso: str, settings) -> None:
    """Execute the full evaluation pipeline for *month*.

    Separated from run() so it can raise freely — safe_run in the
    scheduler catches exceptions and fires the crash notification.
    """
    import anthropic as _anthropic

    from database.db import Database
    from database.repositories.decision_scores_repository import DecisionScoresRepository
    from database.repositories.judge_spot_checks_repository import JudgeSpotChecksRepository
    from database.repositories.monthly_evaluations_repository import (
        MonthlyEvaluationsRepository,
    )
    from evaluation.aggregator import aggregate_scores
    from evaluation.flag_detector import detect_flags
    from evaluation.scorer_programmatic import ProgrammaticScorer
    from evaluation.scoring_orchestrator import (
        score_decisions_in_range,
        score_decisions_with_judge,
    )

    db = Database(path=str(settings.DATABASE_PATH))
    db.init_schema()
    conn = db.get_connection()
    try:
        scores_repo = DecisionScoresRepository(conn)
        spot_checks_repo = JudgeSpotChecksRepository(conn)
        evals_repo = MonthlyEvaluationsRepository(conn)

        # 1. Programmatic scoring (idempotent — skips already-scored decisions)
        prog_scorer = ProgrammaticScorer()
        prog_count = score_decisions_in_range(start_iso, end_iso, prog_scorer, scores_repo)
        logger.info("monthly_evaluation: programmatic scorer wrote %d rows", prog_count)

        # 2. Judge scoring (if EVALUATION_JUDGE_ENABLED)
        if settings.EVALUATION_JUDGE_ENABLED:
            try:
                from evaluation.scorer_judge import JudgeScorer

                judge_scorer = JudgeScorer(
                    model=settings.JUDGE_MODEL,
                    client=_anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY),
                )
                judge_count = score_decisions_with_judge(
                    start_iso,
                    end_iso,
                    judge_scorer,
                    scores_repo,
                    rate_limit_ms=settings.JUDGE_RATE_LIMIT_MS,
                )
                logger.info(
                    "monthly_evaluation: judge scorer wrote %d rows", judge_count
                )
            except Exception:
                logger.exception(
                    "monthly_evaluation: judge scorer failed — continuing without judge scores"
                )

        # 3. Aggregate
        aggregate = aggregate_scores(month, scores_repo)
        overall = aggregate.get("overall", {})
        decisions_scored = overall.get("decisions_scored", 0)
        closed_trades = overall.get("closed_trades_in_window", 0)

        # 4. Load prior 3-month aggregates for trend detection
        history = _load_prior_aggregates(month, scores_repo, evals_repo)

        # 5. Detect flags
        all_flags = detect_flags(aggregate, history)
        real_flags = [f for f in all_flags if not f.get("insufficient_sample")]
        has_insufficient_sample = any(f.get("insufficient_sample") for f in all_flags)

        logger.info(
            "monthly_evaluation: %d flags, insufficient_sample=%s",
            len(real_flags),
            has_insufficient_sample,
        )

        # 6. Spot-check sample
        spot_count = _mark_spot_check_sample(month, scores_repo)
        logger.info(
            "monthly_evaluation: marked %d score rows spot_check_pending", spot_count
        )

        # 7. Prior month judge-operator disagreement rate
        prior_month = _decrement_month(month)
        disagreement_rate = spot_checks_repo.get_disagreement_rate(prior_month)
        logger.info(
            "monthly_evaluation: prior month (%s) disagreement_rate=%.4f",
            prior_month,
            disagreement_rate or 0.0,
        )

        # 8. Compute avg_score / pct_pass from raw score rows
        score_rows = scores_repo.get_by_month(month)
        avg_score, pct_pass = _compute_avg_and_pct_pass(score_rows)

        # 9. UPSERT monthly_evaluations (preserves reviewed_at / action_note)
        created_at = datetime.now(_UTC).isoformat(timespec="seconds")
        eval_row = {
            "month": month,
            "decisions_evaluated": decisions_scored,
            "avg_score": round(avg_score, 2) if avg_score is not None else None,
            "pct_pass": round(pct_pass, 4) if pct_pass is not None else None,
            "score_distribution_json": json.dumps(
                {
                    "by_strategy": aggregate.get("by_strategy", {}),
                    "overall": aggregate.get("overall", {}),
                }
            ),
            "flags_json": json.dumps(real_flags),
            "created_at": created_at,
        }
        evals_repo.upsert(eval_row)
        logger.info("monthly_evaluation: upserted monthly_evaluations row for %s", month)

        # 10. Markdown archive
        _write_markdown_archive(
            month=month,
            aggregate=aggregate,
            flags=real_flags,
            has_insufficient_sample=has_insufficient_sample,
            decisions_scored=decisions_scored,
            closed_trades=closed_trades,
            disagreement_rate=disagreement_rate,
            created_at=created_at,
            reports_dir=settings.REPORTS_DIR,
        )

        # 11. ntfy notification — only when flags are present
        if real_flags:
            strategy_set = {f["strategy"] for f in real_flags}
            try:
                from notifications import notify

                notify(
                    "warning",
                    f"Monthly eval {month}: {len(real_flags)} flags",
                    (
                        f"{len(real_flags)} flags across "
                        f"{len(strategy_set)} strategies. "
                        f"Review at /evaluations/{month}."
                    ),
                    tags=["evaluation", "monthly_review"],
                )
                logger.info(
                    "monthly_evaluation: ntfy notification sent (%d flags)", len(real_flags)
                )
            except Exception:
                logger.warning(
                    "monthly_evaluation: notification failed (non-fatal)", exc_info=True
                )
        else:
            logger.info(
                "monthly_evaluation: no actionable flags — notification suppressed"
            )

    finally:
        db.close()


# ── Public entry point ────────────────────────────────────────────────────────


def run(month: str | None = None) -> None:
    """Run the monthly evaluation pipeline.

    Args:
        month: Target month in YYYY-MM format.  When omitted the prior
               calendar month is used (e.g. on 2026-05-01 → 2026-04).

    The job short-circuits cleanly when EVALUATION_AUTOMATION_ENABLED=false.
    Exceptions propagate so the scheduler's safe_run can fire a crash alert.
    """
    from config import settings

    if not settings.EVALUATION_AUTOMATION_ENABLED:
        logger.info(
            "monthly_evaluation: EVALUATION_AUTOMATION_ENABLED is false — "
            "nothing to do. Set EVALUATION_AUTOMATION_ENABLED=true to enable."
        )
        return

    today = datetime.now(_ET).date()
    eval_month = month or _prior_calendar_month(today)
    start_iso, end_iso = _month_window(eval_month)

    logger.info(
        "monthly_evaluation: starting pipeline for %s (%s → %s)",
        eval_month,
        start_iso,
        end_iso,
    )
    _run_pipeline(eval_month, start_iso, end_iso, settings)
    logger.info("monthly_evaluation: pipeline complete for %s", eval_month)
