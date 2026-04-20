import { useRef } from 'react'
import { PageAudioPlayer } from '../components/shared/PageAudioPlayer'

function PageHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h1>
      <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
        {subtitle}
      </p>
    </div>
  )
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2
      className="text-xs uppercase tracking-wider font-semibold mt-8 mb-3"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h2>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="p-4 rounded-lg border"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      {children}
    </div>
  )
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
      {children}
    </h3>
  )
}

function CardBody({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
      {children}
    </p>
  )
}

function SubLabel({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="text-[10px] uppercase tracking-wider font-semibold mt-4 mb-1"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </p>
  )
}

function StatusBadge({ status }: { status: 'live' | 'in-progress' | 'planned' }) {
  const cfg = {
    live: { label: 'Live', color: 'var(--green)' },
    'in-progress': { label: 'In Progress', color: 'var(--yellow)' },
    planned: { label: 'Planned', color: 'var(--text-muted)' },
  }[status]
  return (
    <span
      className="shrink-0 inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded"
      style={{
        backgroundColor: `color-mix(in srgb, ${cfg.color} 15%, transparent)`,
        color: cfg.color,
        border: `1px solid color-mix(in srgb, ${cfg.color} 25%, transparent)`,
      }}
    >
      ● {cfg.label}
    </span>
  )
}

function KillSwitch({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="mt-4 p-3 rounded text-xs"
      style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
    >
      <span style={{ color: 'var(--text-muted)' }}>Kill switch: </span>
      {children}
    </div>
  )
}

export function Measurement() {
  const contentRef = useRef<HTMLDivElement>(null)

  return (
    <div ref={contentRef} className="max-w-5xl">
      <PageHeader
        title="Measurement"
        subtitle="Why should I trust the numbers this bot produces?"
      />

      {/* Fill Realism Measurement */}
      <SectionHeader>Fill Realism Measurement</SectionHeader>
      <Card>
        <div className="flex items-start justify-between gap-4 mb-3">
          <CardTitle>
            Paper P&amp;L is inflated by different amounts for different strategies — this measures how
            much.
          </CardTitle>
          <StatusBadge status="planned" />
        </div>
        <SubLabel>What it does</SubLabel>
        <CardBody>
          At every order submission, captures the option's bid, ask, and mid. Recaptures at +30 seconds,
          +2 minutes, +15 minutes, and end of day. Compares the submitted limit price to each snapshot and
          classifies whether the order would have realistically filled in a live market. Produces a
          per-(strategy, symbol) fill realism score.
        </CardBody>
        <SubLabel>Why it matters</SubLabel>
        <CardBody>
          Alpaca's paper engine fills at mid with no slippage and no liquidity checks. A wheel trade on
          SPY fills realistically at mid; an iron condor on a thin symbol often wouldn't. Cross-strategy
          paper P&amp;L comparisons are contaminated until this is measured. The real-money promotion gate
          is a fill realism score of 80% or higher across at least 100 closed trades per strategy.
        </CardBody>
        <SubLabel>What you'll see</SubLabel>
        <CardBody>
          A new Fill Realism column on the Strategy Health page. Per-strategy and per-symbol scores once
          enough data has accumulated. This measures what would have happened in a live market — it does
          not change actual fills. Meaningful scores require 100 or more closed trades per strategy, so
          the dataset takes time to build.
        </CardBody>
        <KillSwitch>
          <code className="font-mono" style={{ color: 'var(--text-primary)' }}>
            SHADOW_EXECUTION_ENABLED
          </code>
          <span style={{ color: 'var(--text-muted)' }}> — planned default </span>
          <code className="font-mono" style={{ color: 'var(--text-primary)' }}>
            true
          </code>
        </KillSwitch>
      </Card>

      {/* Adversarial LLM Output Test Suite */}
      <SectionHeader>Adversarial LLM Output Test Suite</SectionHeader>
      <Card>
        <div className="flex items-start justify-between gap-4 mb-3">
          <CardTitle>
            A fixed corpus of pathological Claude responses paired with assertions about correct bot
            handling.
          </CardTitle>
          <StatusBadge status="planned" />
        </div>
        <SubLabel>What it does</SubLabel>
        <CardBody>
          Tests cover malformed JSON, wrong-sign prices on credit spreads, mismatched OCC symbols, swapped
          action and confidence fields, hallucinated reasoning, refusals, truncated output, and debit/credit
          confusion. Each test pairs a pathological-but-possible Claude response with an assertion about
          what the bot must do with it. Runs in CI on every push.
        </CardBody>
        <SubLabel>Why it matters</SubLabel>
        <CardBody>
          When Claude is upgraded to a new model version, output characteristics shift in subtle ways —
          different JSON quirks, different refusal wording, different edge-case behavior. Without a fixed
          test corpus, these shifts get discovered in production via a crash or a silently-bad decision.
          The adversarial suite must be green before any model upgrade proceeds.
        </CardBody>
        <SubLabel>What you'll see</SubLabel>
        <CardBody>
          Test results in CI. The corpus lives at{' '}
          <code className="text-xs font-mono">tests/adversarial_llm_outputs/</code>. Failures block
          merges. The corpus grows over time as production surfaces new failure modes — each
          newly-discovered failure becomes a permanent regression test.
        </CardBody>
      </Card>
      <p className="text-xs mt-2 pl-1" style={{ color: 'var(--text-muted)' }}>
        No kill switch — these are tests. Depends on Safe-Decline Fallback shipping first.
      </p>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
