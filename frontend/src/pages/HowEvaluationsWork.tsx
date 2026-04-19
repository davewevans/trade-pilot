import { useRef } from 'react'
import { PageAudioPlayer } from '../components/shared/PageAudioPlayer'

// ── Helpers ────────────────────────────────────────────────────────────────────

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2
      className="text-xs uppercase tracking-wider font-semibold mb-4"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h2>
  )
}

function StepBadge({ n }: { n: number }) {
  return (
    <div
      className="w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-sm font-mono font-semibold"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--accent) 20%, transparent)',
        color: 'var(--accent)',
        border: '1px solid color-mix(in srgb, var(--accent) 40%, transparent)',
      }}
    >
      {n}
    </div>
  )
}

function Arrow() {
  return (
    <div className="flex justify-center my-1" aria-hidden="true">
      <span style={{ color: 'var(--text-muted)' }}>↓</span>
    </div>
  )
}

function Card({
  children,
  accent,
  topAccent,
}: {
  children: React.ReactNode
  accent?: string
  topAccent?: string
}) {
  return (
    <div
      className="p-4 rounded-lg border"
      style={{
        backgroundColor: 'var(--bg-card)',
        borderColor: 'var(--border)',
        borderLeft: accent ? `4px solid ${accent}` : undefined,
        borderTop: topAccent ? `3px solid ${topAccent}` : undefined,
      }}
    >
      {children}
    </div>
  )
}

function Callout({
  children,
  accent = 'var(--accent)',
}: {
  children: React.ReactNode
  accent?: string
}) {
  return (
    <div
      className="mt-4 p-4 rounded text-sm leading-relaxed"
      style={{
        backgroundColor: `color-mix(in srgb, ${accent} 8%, transparent)`,
        color: 'var(--text-secondary)',
        borderLeft: `3px solid ${accent}`,
      }}
    >
      {children}
    </div>
  )
}

function P({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm leading-relaxed mb-3" style={{ color: 'var(--text-secondary)' }}>
      {children}
    </p>
  )
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
      {children}
    </h3>
  )
}

function BulletList({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="space-y-1.5 mt-2">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2 text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          <span style={{ color: 'var(--accent)', flexShrink: 0 }}>•</span>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

// ── Data ───────────────────────────────────────────────────────────────────────

const PIPELINE_STEPS = [
  {
    num: 1,
    label: 'Programmatic scoring',
    body: 'ProgrammaticScorer scores every entry decision (SELL_PUT / SELL_CALL / OPEN) on rule_adherence and every SKIP on skip_validity_structural.',
  },
  {
    num: 2,
    label: 'Judge scoring',
    body: 'If EVALUATION_JUDGE_ENABLED=true, Claude Opus scores each decision on 5 qualitative dimensions: reasoning groundedness, relevance, specificity; confidence calibration; context utilization.',
  },
  {
    num: 3,
    label: 'Aggregate',
    body: 'Scores roll up by strategy × dimension (mean, median, stddev, N) plus a per-prompt-version breakdown.',
  },
  {
    num: 4,
    label: 'Load history',
    body: 'Prior 3 months of aggregates are loaded for trend detection.',
  },
  {
    num: 5,
    label: 'Detect flags',
    body: 'Each strategy that meets its sample-size gate gets two checks: absolute threshold (mean below floor) and negative trend (monotonic decline ≥ 0.05 across 3 months).',
  },
  {
    num: 6,
    label: 'Spot-check sampling',
    body: '15% of score rows are randomly marked spot_check_pending for operator review.',
  },
  {
    num: 7,
    label: 'Disagreement rate',
    body: "Prior month's judge-vs-operator disagreement % is pulled from judge_spot_checks.",
  },
  {
    num: 8,
    label: 'Upsert monthly_evaluations',
    body: 'Written atomically; reviewed_at and action_note are preserved on re-runs.',
  },
  {
    num: 9,
    label: 'Markdown archive',
    body: 'Written to data/reports/monthly_eval_YYYY-MM.md.',
  },
  {
    num: 10,
    label: 'Notification',
    body: 'ntfy warning fires only when real flags exist. Clean months and insufficient-sample months are silent.',
  },
]

const SAMPLE_GATES = [
  { strategy: 'wheel', gate: 10 },
  { strategy: 'turnover_wheel', gate: 10 },
  { strategy: 'bull_put_spread', gate: 10 },
  { strategy: 'bear_call_spread', gate: 10 },
  { strategy: 'long_call_vertical', gate: 5 },
  { strategy: 'iron_condor', gate: 5 },
  { strategy: 'iron_butterfly', gate: 5 },
  { strategy: 'calendar_spread', gate: 5 },
  { strategy: '(unknown strategy)', gate: 10 },
]

const ABS_THRESHOLDS = [
  { dimension: 'rule_adherence', floor: '0.70' },
  { dimension: 'skip_validity_structural', floor: '0.80' },
  { dimension: 'reasoning_groundedness', floor: '0.60' },
  { dimension: 'reasoning_relevance', floor: '0.60' },
  { dimension: 'reasoning_specificity', floor: '0.60' },
  { dimension: 'confidence_calibration', floor: '0.60' },
  { dimension: 'context_utilization', floor: '0.60' },
]

const WHERE_TO_LOOK = [
  {
    path: '/evaluations',
    desc: 'Monthly evaluations list, newest first. Columns: decisions, closed trades, flag count, judge-operator disagreement, review status.',
  },
  {
    path: '/evaluations/:month',
    desc: 'Per-month detail. Score distribution charts, flagged dimensions with lowest decisions, spot-check queue, mark-reviewed flow.',
  },
  {
    path: '/decisions',
    desc: 'Source-of-truth for individual decisions that get scored. Every row that feeds the evaluation came from here.',
  },
  {
    path: '/strategy-health',
    desc: 'Complementary week-by-week funnel view. Evaluations grade decision quality; strategy health tracks volume and where it drops off.',
  },
  {
    path: 'data/reports/monthly_eval_YYYY-MM.md',
    desc: 'Markdown archive on disk. Same content as the detail page, useful for diffing months or pasting into other contexts.',
  },
]

// ── Component ──────────────────────────────────────────────────────────────────

export function HowEvaluationsWork() {
  const contentRef = useRef<HTMLDivElement>(null)

  return (
    <div ref={contentRef} className="max-w-5xl">

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="mb-8">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
          How Evaluations Work
        </h1>
        <p className="text-sm mt-2 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          How Claude's trading decisions get scored, flagged, and reviewed every month.
        </p>
      </div>

      {/* ── Section 1: Why This Exists ──────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Why This Exists</SectionHeader>
        <div
          className="p-5 rounded-lg border space-y-3"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            Weekly reports tell you what happened — which trades were opened, which were skipped, and
            what the running P&amp;L looks like. They don't tell you whether Claude's reasoning was
            sound on a per-decision basis, or whether rule adherence is drifting over time.
          </p>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            The monthly review system runs offline on the 1st of each month, scores every decision
            from the prior month against the v1.0.0 rubric, and surfaces any quality drift as flags
            the operator reviews.
          </p>
        </div>
        <Callout>
          <strong style={{ color: 'var(--text-primary)' }}>This is not an auto-tuning loop.</strong>{' '}
          Nothing here modifies{' '}
          <code className="font-mono text-xs">prompts/system.md</code> or strategy thresholds. It's
          a drift-detection and review layer — the operator still writes the prompt changes. The
          system just makes sure drift gets noticed systematically.
        </Callout>
      </section>

      {/* ── Section 2: The Pipeline ─────────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>The Pipeline</SectionHeader>
        <div
          className="p-5 rounded-lg border mb-4"
          style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
        >
          {PIPELINE_STEPS.map((s, i) => (
            <div key={s.num}>
              <div
                className="p-3 rounded-lg border flex gap-3 items-start"
                style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
              >
                <StepBadge n={s.num} />
                <div className="min-w-0">
                  <div className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                    {s.label}
                  </div>
                  <p className="text-sm leading-relaxed mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                    {s.body}
                  </p>
                </div>
              </div>
              {i < PIPELINE_STEPS.length - 1 && <Arrow />}
            </div>
          ))}
        </div>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Scheduled at 05:00 ET on day 1 of each month. The scheduler registers a daily 05:00 ET
          trigger with a wrapper that short-circuits on days 2–31, so the pipeline never runs
          mid-month by accident. Everything is gated by{' '}
          <code className="font-mono text-xs">EVALUATION_AUTOMATION_ENABLED</code>.
        </p>
      </section>

      {/* ── Section 3: What Gets Scored ─────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>What Gets Scored</SectionHeader>
        <div className="grid gap-3 md:grid-cols-2">

          {/* Programmatic */}
          <Card topAccent="var(--green)">
            <CardTitle>Programmatic dimensions</CardTitle>
            <div className="space-y-3 mt-1">
              <div>
                <div className="text-sm font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  <code className="font-mono text-xs">rule_adherence</code>
                </div>
                <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                  For entry decisions. Checks delta range, DTE range, IVR floor, earnings buffer,
                  open interest, and credit/debit against strategy params. Score = rules_passed /
                  rules_checked. Boundaries pass — delta = 0.20 with minimum 0.20 is a pass.
                </p>
              </div>
              <div>
                <div className="text-sm font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  <code className="font-mono text-xs">skip_validity_structural</code>
                </div>
                <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                  For SKIP decisions. Validates{' '}
                  <code className="font-mono text-xs">skip_reason_code</code> is a known enum and
                  that the context is consistent with it — for example,{' '}
                  <code className="font-mono text-xs">EARNINGS_TOO_CLOSE</code> only passes if
                  days_until_earnings is within the block window.
                </p>
              </div>
            </div>
          </Card>

          {/* Judge */}
          <Card topAccent="var(--accent)">
            <CardTitle>Judge dimensions</CardTitle>
            <BulletList
              items={[
                <><strong style={{ color: 'var(--text-primary)' }}>reasoning_groundedness</strong> — Is the reasoning supported by the context provided?</>,
                <><strong style={{ color: 'var(--text-primary)' }}>reasoning_relevance</strong> — Does the reasoning address what actually matters for this decision?</>,
                <><strong style={{ color: 'var(--text-primary)' }}>reasoning_specificity</strong> — Is it concrete (specific numbers, contracts, conditions) or vague?</>,
                <><strong style={{ color: 'var(--text-primary)' }}>confidence_calibration</strong> — Does stated confidence match the evidence quality?</>,
                <><strong style={{ color: 'var(--text-primary)' }}>context_utilization</strong> — Did Claude actually use the context, or ignore it?</>,
              ]}
            />
            <p className="text-xs mt-3 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              Judge runs on Claude Opus 4.7. A more-capable model judging the trader model. Costs
              real money — gated behind{' '}
              <code className="font-mono">EVALUATION_JUDGE_ENABLED</code>.
            </p>
          </Card>

        </div>
      </section>

      {/* ── Section 4: Sample-Size Gates ────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Sample-Size Gates</SectionHeader>
        <p className="text-sm leading-relaxed mb-4" style={{ color: 'var(--text-secondary)' }}>
          Flags are suppressed when a strategy has too few scored decisions in a month.
          Low-sample strategies receive an{' '}
          <code className="font-mono text-xs">insufficient_sample</code> marker instead of flags.
          This prevents the system from treating noise from thin data as a real quality problem.
        </p>
        <div
          className="rounded-lg border divide-y"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <div
            className="px-4 py-2 grid gap-2 md:grid-cols-[1fr_8rem]"
            style={{ borderColor: 'var(--border)' }}
          >
            <span
              className="text-[10px] uppercase tracking-wider font-semibold"
              style={{ color: 'var(--text-muted)' }}
            >
              Strategy
            </span>
            <span
              className="text-[10px] uppercase tracking-wider font-semibold"
              style={{ color: 'var(--text-muted)' }}
            >
              Min. decisions
            </span>
          </div>
          {SAMPLE_GATES.map((row) => (
            <div
              key={row.strategy}
              className="px-4 py-3 grid gap-2 md:grid-cols-[1fr_8rem]"
              style={{ borderColor: 'var(--border)' }}
            >
              <code className="font-mono text-sm" style={{ color: 'var(--text-primary)' }}>
                {row.strategy}
              </code>
              <span className="font-mono text-sm font-semibold" style={{ color: 'var(--accent)' }}>
                {row.gate}
              </span>
            </div>
          ))}
        </div>
        <Callout>
          With current paper-trading volume, expect the first several months to hit
          insufficient-sample markers for at least some strategies. That's not failure — it's the
          system refusing to flag anything from too-thin data.
        </Callout>
      </section>

      {/* ── Section 5: Flag Detection ───────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Flag Detection</SectionHeader>
        <div className="grid gap-3 md:grid-cols-2">

          {/* Absolute threshold */}
          <Card>
            <CardTitle>Absolute threshold</CardTitle>
            <P>
              A flag fires when{' '}
              <code className="font-mono text-xs">mean(dimension_score) &lt; threshold</code>.
            </P>
            <div
              className="rounded border divide-y"
              style={{ borderColor: 'var(--border)' }}
            >
              {ABS_THRESHOLDS.map((row) => (
                <div
                  key={row.dimension}
                  className="px-3 py-2 grid gap-2 md:grid-cols-[1fr_4rem]"
                  style={{ borderColor: 'var(--border)' }}
                >
                  <code className="font-mono text-xs" style={{ color: 'var(--text-primary)' }}>
                    {row.dimension}
                  </code>
                  <span
                    className="font-mono text-xs font-semibold"
                    style={{ color: 'var(--accent)' }}
                  >
                    {row.floor}
                  </span>
                </div>
              ))}
            </div>
            <Callout accent="var(--yellow, #eab308)">
              All thresholds are marked "calibrate after first pass" in{' '}
              <code className="font-mono text-xs">evaluation/thresholds.py</code>. These are
              starting guesses, not validated against a real score distribution. Review after the
              first full month of data.
            </Callout>
          </Card>

          {/* Negative trend */}
          <Card>
            <CardTitle>Negative trend</CardTitle>
            <P>
              Fires when the dimension mean declines monotonically across 3 consecutive months with
              a total drop of 0.05 or more. Requires 3 months of history — no trend flags until 3
              months after the first run.
            </P>
            <P>
              The trend window needs the current month plus the 2 prior months to form a complete
              series. If any month in the series has no data for that strategy/dimension, the trend
              check is skipped rather than errored.
            </P>
          </Card>

        </div>
      </section>

      {/* ── Section 6: The Spot-Check Workflow ──────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>The Spot-Check Workflow</SectionHeader>
        <div
          className="p-5 rounded-lg border"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <ol className="space-y-3">
            {[
              'Each month, 15% of score rows get spot_check_pending=1.',
              "On the month's detail page, open the Spot-Check Queue. Each entry shows the decision, the judge's score, and the judge's justification. Vote Agree, Disagree, or Unclear — with an optional note.",
              "Disagreements accumulate into the judge_operator_disagreement rate shown on the month after (the current month reports last month's rate).",
              'Persistent high disagreement rate signals that the judge prompt or rubric needs revision, not Claude.',
            ].map((item, i) => (
              <li
                key={i}
                className="flex gap-3 text-sm leading-relaxed"
                style={{ color: 'var(--text-secondary)' }}
              >
                <span
                  className="w-6 h-6 shrink-0 rounded-full flex items-center justify-center text-xs font-mono font-semibold mt-0.5"
                  style={{
                    backgroundColor: 'color-mix(in srgb, var(--accent) 20%, transparent)',
                    color: 'var(--accent)',
                    border: '1px solid color-mix(in srgb, var(--accent) 40%, transparent)',
                  }}
                >
                  {i + 1}
                </span>
                <span>{item}</span>
              </li>
            ))}
          </ol>
        </div>
        <Callout>
          The spot-check rate is a calibration signal for the judge, not a grading signal for Claude.
        </Callout>
      </section>

      {/* ── Section 7: Feature Flags ────────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Feature Flags</SectionHeader>
        <div className="grid gap-3 md:grid-cols-3">
          {[
            {
              key: 'EVALUATION_AUTOMATION_ENABLED',
              body: 'Gates the whole monthly pipeline. Default false.',
            },
            {
              key: 'EVALUATION_JUDGE_ENABLED',
              body: 'Gates the Opus judge (costs real money). Default false.',
            },
            {
              key: 'JUDGE_MODEL / JUDGE_RATE_LIMIT_MS',
              body: 'Judge model override and rate limit. Defaults: claude-opus-4-7, 200 ms.',
            },
          ].map((f) => (
            <div
              key={f.key}
              className="p-4 rounded-lg border"
              style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
            >
              <div
                className="font-mono text-xs font-semibold mb-2 break-all"
                style={{ color: 'var(--accent)' }}
              >
                {f.key}
              </div>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {f.body}
              </p>
            </div>
          ))}
        </div>
        <p className="text-sm leading-relaxed mt-4" style={{ color: 'var(--text-secondary)' }}>
          If the pipeline runs but both scorers are off, you get an empty{' '}
          <code className="font-mono text-xs">monthly_evaluations</code> row. To backfill an old
          month, use{' '}
          <code className="font-mono text-xs">python main.py --job=monthly_evaluation</code> with a{' '}
          <code className="font-mono text-xs">month=YYYY-MM</code> override — the upsert preserves
          your review notes.
        </p>
      </section>

      {/* ── Section 8: How to Review a Month ────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>How to Review a Month</SectionHeader>
        <div
          className="p-5 rounded-lg border"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <ol className="space-y-3">
            {([
              <>Open <code className="font-mono text-xs">/evaluations</code> and click the month.</>,
              'Check the insufficient-sample banner. If present, flags below may not be meaningful.',
              'Review each flagged dimension. Expand to see the 5 lowest-scoring decisions.',
              'Work the spot-check queue. Submit at least a few verdicts so the disagreement rate has signal next month.',
              <>Decide: is this drift real? If yes, update <code className="font-mono text-xs">prompts/system.md</code> or the relevant strategy prompt. If no (e.g., small sample noise), note it in the action note and move on.</>,
              'Click "Mark reviewed" with an action note describing any prompt or rule changes made, or why none were needed. That note is the audit trail — treat it as real documentation.',
            ] as React.ReactNode[]).map((item, i) => (
              <li
                key={i}
                className="flex gap-3 text-sm leading-relaxed"
                style={{ color: 'var(--text-secondary)' }}
              >
                <span
                  className="w-6 h-6 shrink-0 rounded-full flex items-center justify-center text-xs font-mono font-semibold mt-0.5"
                  style={{
                    backgroundColor: 'color-mix(in srgb, var(--accent) 20%, transparent)',
                    color: 'var(--accent)',
                    border: '1px solid color-mix(in srgb, var(--accent) 40%, transparent)',
                  }}
                >
                  {i + 1}
                </span>
                <span>{item}</span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ── Section 9: Where to Look ────────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Where to Look</SectionHeader>
        <div
          className="rounded-lg border divide-y"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          {WHERE_TO_LOOK.map((row) => (
            <div
              key={row.path}
              className="px-4 py-3 grid gap-1 md:gap-4 md:grid-cols-[14rem_1fr]"
              style={{ borderColor: 'var(--border)' }}
            >
              <code
                className="font-mono text-sm font-semibold"
                style={{ color: 'var(--accent)' }}
              >
                {row.path}
              </code>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {row.desc}
              </p>
            </div>
          ))}
        </div>
      </section>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
