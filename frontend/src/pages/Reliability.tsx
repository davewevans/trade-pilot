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

export function Reliability() {
  const contentRef = useRef<HTMLDivElement>(null)

  return (
    <div ref={contentRef} className="max-w-5xl">
      <PageHeader
        title="Reliability"
        subtitle="What stops the bot from making damaging decisions when its inputs are wrong?"
      />

      {/* Safe-Decline Fallback */}
      <SectionHeader>Safe-Decline Fallback</SectionHeader>
      <Card>
        <div className="flex items-start justify-between gap-4 mb-3">
          <CardTitle>Schema validation failures return a typed skip, not a crash.</CardTitle>
          <StatusBadge status="planned" />
        </div>
        <SubLabel>What it does</SubLabel>
        <CardBody>
          When Claude's response fails schema validation twice in a row, the bot returns a typed SKIP
          decision instead of crashing the decision cycle for that symbol. The raw failed response is
          appended to a failure log for later inspection.
        </CardBody>
        <SubLabel>Why it matters</SubLabel>
        <CardBody>
          A single malformed Claude response previously broke the entire decision cycle for a symbol,
          surfacing as an unhandled exception. The failure log is also the foundation of the adversarial
          test corpus — patterns in production failures reveal what Claude gets wrong, which become
          permanent regression tests.
        </CardBody>
        <SubLabel>What you'll see</SubLabel>
        <CardBody>
          A new <code className="text-xs font-mono">SCHEMA_INVALID</code> skip code in decision logs.
          Raw failed responses appended to{' '}
          <code className="text-xs font-mono">data/logs/schema_failures.jsonl</code>.
        </CardBody>
      </Card>
      <p className="text-xs mt-2 pl-1" style={{ color: 'var(--text-muted)' }}>
        No kill switch — replacing a crash with a logged skip is strictly better with no tradeoff.
      </p>

      {/* Clock Drift Fail-Safe */}
      <SectionHeader>Clock Drift Fail-Safe</SectionHeader>
      <Card>
        <div className="flex items-start justify-between gap-4 mb-3">
          <CardTitle>
            The bot halts if its clock drifts more than 30 seconds from Alpaca's server time.
          </CardTitle>
          <StatusBadge status="planned" />
        </div>
        <SubLabel>What it does</SubLabel>
        <CardBody>
          Before every scheduled job, the bot compares its local clock to Alpaca's server clock. If drift
          exceeds 30 seconds, it writes{' '}
          <code className="text-xs font-mono">data/HALTED.lock</code> and exits without running the job.
          Manual resume is required: delete the lock file.
        </CardBody>
        <SubLabel>Why it matters</SubLabel>
        <CardBody>
          Scheduler jobs are time-pinned to Eastern Time. Clock drift can run{' '}
          <code className="text-xs font-mono">market_open</code> during pre-open (bad fills, unreliable
          quotes), miss the expiry guard window entirely, or compute stale timing values. Halting on
          detected drift is cheap insurance against a category of failure that is invisible until it costs
          money. The check fails open on transient network failure — halting on a network blip would be
          worse than the drift the check protects against.
        </CardBody>
        <SubLabel>What you'll see</SubLabel>
        <CardBody>
          A <code className="text-xs font-mono">HALTED.lock</code> file with reason{' '}
          <code className="text-xs font-mono">clock_drift_&lt;N&gt;s</code> if it ever fires. A
          critical-level log entry. The bot must be manually resumed by deleting the lock file.
        </CardBody>
        <KillSwitch>
          <code className="font-mono" style={{ color: 'var(--text-primary)' }}>
            CLOCK_DRIFT_HALT_ENABLED
          </code>
          <span style={{ color: 'var(--text-muted)' }}> — planned default </span>
          <code className="font-mono" style={{ color: 'var(--text-primary)' }}>
            true
          </code>
        </KillSwitch>
      </Card>

      {/* State Reconciliation */}
      <SectionHeader>State Reconciliation</SectionHeader>
      <Card>
        <div className="flex items-start justify-between gap-4 mb-3">
          <CardTitle>
            Broker reality overwrites local state at boot and is verified every 5 minutes during market
            hours.
          </CardTitle>
          <StatusBadge status="planned" />
        </div>

        <p className="text-sm font-medium mt-1 mb-1" style={{ color: 'var(--text-primary)' }}>
          Startup Recovery
        </p>
        <SubLabel>What it does</SubLabel>
        <CardBody>
          Runs once on scheduler boot, before any trading job fires. Per account: fetches positions,
          orders, cash, and buying power from Alpaca. Rebuilds strategy state machines from broker truth
          and overwrites SQLite on mismatch. On a critical mismatch, halts.
        </CardBody>

        <p className="text-sm font-medium mt-4 mb-1" style={{ color: 'var(--text-primary)' }}>
          Drop-Copy Monitoring
        </p>
        <SubLabel>What it does</SubLabel>
        <CardBody>
          Extends the existing reconcile job to run every 5 minutes during market hours. Compares
          per-account broker state against SQLite with a tiered response: a position value mismatch
          greater than $100 or 1% of equity triggers a yellow alert and blocks new entries until cleared;
          an untracked broker position or unknown filled order triggers a red alert and full halt.
        </CardBody>

        <SubLabel>Why it matters</SubLabel>
        <CardBody>
          SQLite diverging from broker reality is the silent-money-vanishing failure mode. A partial fill
          during a restart, a snapshot-then-crash, or a missed assignment event can leave the bot making
          decisions against false local state. Broker truth is authoritative.
        </CardBody>
        <SubLabel>What you'll see</SubLabel>
        <CardBody>
          Startup writes a diff report to{' '}
          <code className="text-xs font-mono">data/snapshots/startup_reconcile_report.json</code>.
          Drop-copy mismatches appear as ntfy alerts and dashboard status changes. Hard halts surface via{' '}
          <code className="text-xs font-mono">HALTED.lock</code>. Ships in log-only mode for the first
          week — mismatch distributions get observed before thresholds tighten to enforcement.
        </CardBody>
        <KillSwitch>
          <span style={{ color: 'var(--text-muted)' }}>
            Three switches (all planned):{' '}
          </span>
          <code className="font-mono" style={{ color: 'var(--text-primary)' }}>
            STARTUP_RECONCILE_ENABLED
          </code>
          <span style={{ color: 'var(--text-muted)' }}>, </span>
          <code className="font-mono" style={{ color: 'var(--text-primary)' }}>
            DROP_COPY_RECONCILE_ENABLED
          </code>
          <span style={{ color: 'var(--text-muted)' }}>, </span>
          <code className="font-mono" style={{ color: 'var(--text-primary)' }}>
            STARTUP_RECONCILE_HALT_THRESHOLD_USD
          </code>
        </KillSwitch>
      </Card>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
