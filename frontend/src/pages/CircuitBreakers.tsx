import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { PageAudioPlayer } from '../components/shared/PageAudioPlayer'
import { useCanMutate } from '../context/AuthContext'

type Threshold = {
  level: string
  trigger: string
  effect: string
  status: string
  emoji: string
  color: string
}

const THRESHOLDS: Threshold[] = [
  {
    level: 'Normal',
    trigger: 'No thresholds breached',
    effect: 'Full trading. All strategies can open new positions.',
    status: 'GREEN',
    emoji: '🟢',
    color: 'var(--green)',
  },
  {
    level: 'Daily loss — reduce',
    trigger: 'Portfolio down ≥ 1.5% today',
    effect: 'Position sizing cut in half. New positions can still open but at 50% normal size.',
    status: 'YELLOW',
    emoji: '🟡',
    color: 'var(--yellow, #eab308)',
  },
  {
    level: 'Daily loss — halt',
    trigger: 'Portfolio down ≥ 3.0% today',
    effect: 'No new positions for the rest of today. Existing positions still managed. Resets at next market open.',
    status: 'RED',
    emoji: '🔴',
    color: 'var(--red)',
  },
  {
    level: 'Weekly loss — halt',
    trigger: 'Portfolio down ≥ 5.0% this week',
    effect: 'No new positions for the rest of the week. Resets Monday morning.',
    status: 'RED',
    emoji: '🔴',
    color: 'var(--red)',
  },
  {
    level: 'Drawdown — halt',
    trigger: 'Portfolio down ≥ 10.0% from peak',
    effect: 'No new positions until equity recovers or the week resets.',
    status: 'RED',
    emoji: '🔴',
    color: 'var(--red)',
  },
  {
    level: 'Drawdown — lock',
    trigger: 'Portfolio down ≥ 15.0% from peak',
    effect:
      'Full stop. Bot writes a lock file. Trading is completely halted until a human manually deletes the lock file. No automatic reset.',
    status: 'HALTED',
    emoji: '🔴',
    color: 'var(--red)',
  },
]

const ACCOUNT_STATUS = [
  {
    status: 'GREEN',
    emoji: '🟢',
    color: 'var(--green)',
    wheel: 'New CSPs and CCs allowed',
    condor: 'New condors allowed',
    spreads: 'New spreads allowed (one per cycle)',
  },
  {
    status: 'YELLOW',
    emoji: '🟡',
    color: 'var(--yellow, #eab308)',
    wheel: 'No new positions; existing managed',
    condor: 'No new positions; existing managed',
    spreads: 'No new positions; existing managed',
  },
  {
    status: 'RED',
    emoji: '🔴',
    color: 'var(--red)',
    wheel: 'No new positions; existing managed',
    condor: 'No new positions; existing managed',
    spreads: 'No new positions; existing managed',
  },
  {
    status: 'HALTED',
    emoji: '🔴',
    color: 'var(--red)',
    wheel: 'All activity stopped',
    condor: 'All activity stopped',
    spreads: 'All activity stopped',
  },
]

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

function ResetCard({
  num,
  title,
  children,
}: {
  num: number
  title: string
  children: React.ReactNode
}) {
  return (
    <div
      className="p-4 rounded-lg border h-full"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <div className="flex items-center gap-2 mb-2">
        <span
          className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-mono font-semibold"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 20%, transparent)',
            color: 'var(--accent)',
          }}
        >
          {num}
        </span>
        <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
          {title}
        </h3>
      </div>
      <div className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {children}
      </div>
    </div>
  )
}

export function CircuitBreakers() {
  const canMutate = useCanMutate()
  const [status, setStatus] = useState<string | null>(null)
  const [halted, setHalted] = useState<boolean>(false)
  const [dryRun, setDryRun] = useState<boolean>(false)
  const [resetting, setResetting] = useState(false)
  const [resetMsg, setResetMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.circuitBreakers().catch(() => null),
      api.health().catch(() => null),
    ]).then(([cb, h]) => {
      if (cancelled) return
      setStatus(cb?.status ?? null)
      setHalted(Boolean(h?.halted))
      setDryRun(Boolean(cb?.dry_run))
    })
    return () => {
      cancelled = true
    }
  }, [])

  const showReset = !dryRun && (halted || status === 'RED' || status === 'HALTED')

  async function handleReset() {
    if (
      !window.confirm(
        'This will reset the circuit breaker and allow the bot to resume trading. Continue?',
      )
    ) {
      return
    }
    setResetting(true)
    setResetMsg(null)
    try {
      const r = await api.resetCircuitBreaker()
      setResetMsg({
        type: 'success',
        text: `Reset complete. Deleted: ${r.deleted.length ? r.deleted.join(', ') : 'nothing (already clear)'}. Reloading…`,
      })
      setTimeout(() => window.location.reload(), 1200)
    } catch (err) {
      setResetMsg({
        type: 'error',
        text: err instanceof Error ? err.message : 'Reset failed',
      })
      setResetting(false)
    }
  }

  return (
    <div ref={contentRef} className="max-w-5xl">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
            Circuit Breakers
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
            Portfolio-level safety rules. The last line of defense before real capital is at risk.
          </p>
        </div>
        {canMutate && showReset && (
          <button
            type="button"
            onClick={handleReset}
            disabled={resetting}
            className="text-xs px-3 py-1.5 rounded border whitespace-nowrap disabled:opacity-50"
            style={{ color: 'var(--red)', borderColor: 'var(--red)', backgroundColor: 'transparent' }}
          >
            {resetting ? 'Resetting…' : 'Reset Circuit Breaker'}
          </button>
        )}
      </div>
      {resetMsg && (
        <div
          className="mb-4 p-3 rounded text-xs"
          style={{
            backgroundColor:
              resetMsg.type === 'success'
                ? 'color-mix(in srgb, var(--green) 10%, transparent)'
                : 'color-mix(in srgb, var(--red) 10%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: `3px solid ${resetMsg.type === 'success' ? 'var(--green)' : 'var(--red)'}`,
          }}
        >
          {resetMsg.text}
        </div>
      )}

      {/* Section 1 */}
      <SectionHeader>What Is a Circuit Breaker?</SectionHeader>
      <div
        className="p-4 rounded-lg border"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Circuit breakers are portfolio-level safety rules that run independently of Claude.
          They monitor how much money the portfolio has made or lost — today, this week, and from
          its peak — and progressively reduce or stop trading when losses reach certain thresholds.
          They are the last line of defense before real capital is at risk.
        </p>
      </div>

      {/* Section 2: Thresholds */}
      <SectionHeader>The Thresholds</SectionHeader>

      {/* Desktop */}
      <div
        className="hidden md:block rounded-lg border overflow-x-auto"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
              <th className="px-4 py-3 font-medium">Level</th>
              <th className="px-4 py-3 font-medium">Trigger</th>
              <th className="px-4 py-3 font-medium">What Happens</th>
              <th className="px-4 py-3 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {THRESHOLDS.map((t) => (
              <tr key={t.level} style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                <td className="px-4 py-3 align-top font-medium" style={{ color: 'var(--text-primary)' }}>{t.level}</td>
                <td className="px-4 py-3 align-top">{t.trigger}</td>
                <td className="px-4 py-3 align-top">{t.effect}</td>
                <td className="px-4 py-3 align-top whitespace-nowrap">
                  <span style={{ color: t.color }} className="font-mono text-xs font-semibold">
                    {t.emoji} {t.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile */}
      <div className="md:hidden space-y-2">
        {THRESHOLDS.map((t) => (
          <div
            key={t.level}
            className="p-3 rounded-lg border"
            style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
          >
            <div className="flex justify-between items-start gap-2">
              <div className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>{t.level}</div>
              <span style={{ color: t.color }} className="font-mono text-xs font-semibold whitespace-nowrap">
                {t.emoji} {t.status}
              </span>
            </div>
            <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>{t.trigger}</div>
            <p className="text-sm mt-2" style={{ color: 'var(--text-secondary)' }}>{t.effect}</p>
          </div>
        ))}
      </div>

      <div
        className="mt-3 p-3 rounded text-xs"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--red) 10%, transparent)',
          color: 'var(--text-secondary)',
          borderLeft: '3px solid var(--red)',
        }}
      >
        The 15% drawdown lock is the only threshold that does not reset automatically. It requires
        a human to review the situation and reset via the dashboard button or by deleting the{' '}
        <code className="font-mono">HALTED.lock</code> file before the bot will trade again.
      </div>

      {/* DRY_RUN note */}
      {dryRun && (
        <div
          className="mt-4 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Dry run mode is active.</strong> The circuit breaker calculates all metrics —
          daily P&L, weekly P&L, drawdown from peak — but always returns GREEN status. No
          thresholds can trigger YELLOW, RED, or HALTED while dry run is enabled. This lets
          the bot run through its full decision flow without being blocked by paper-account
          equity data that doesn't reflect real risk. The dashboard header shows{' '}
          <code className="font-mono">(dry run)</code> next to the circuit status badge when
          this bypass is active.
        </div>
      )}
      {!dryRun && (
        <div
          className="mt-4 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Dry run mode:</strong> When <code className="font-mono">DRY_RUN=true</code>,
          the circuit breaker still calculates all metrics but always returns GREEN — no
          thresholds trigger. This lets the bot make decisions without being blocked by
          paper-account equity data. The dashboard shows{' '}
          <code className="font-mono">(dry run)</code> next to the status badge when active.
        </div>
      )}

      {/* Section 3 */}
      <SectionHeader>How Status Affects Each Account</SectionHeader>
      <div
        className="rounded-lg border overflow-x-auto"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Wheel Account</th>
              <th className="px-4 py-3 font-medium">Iron Condor Account</th>
              <th className="px-4 py-3 font-medium">Spreads Account</th>
            </tr>
          </thead>
          <tbody>
            {ACCOUNT_STATUS.map((a) => (
              <tr key={a.status} style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                <td className="px-4 py-3 align-top whitespace-nowrap">
                  <span style={{ color: a.color }} className="font-mono text-xs font-semibold">
                    {a.emoji} {a.status}
                  </span>
                </td>
                <td className="px-4 py-3 align-top">{a.wheel}</td>
                <td className="px-4 py-3 align-top">{a.condor}</td>
                <td className="px-4 py-3 align-top">{a.spreads}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Section 4 */}
      <SectionHeader>How It Resets</SectionHeader>
      <div className="grid gap-3 md:grid-cols-3">
        <ResetCard num={1} title="Daily Reset (automatic)">
          At each market open, the day's starting equity is updated to the current portfolio value.
          The daily loss counter resets to zero. If the bot was in RED due to a daily loss, it
          returns to GREEN the next morning (assuming no other thresholds are active).
        </ResetCard>
        <ResetCard num={2} title="Weekly Reset (automatic)">
          Every Monday at market open, the week's starting equity resets. Weekly loss counters
          restart from zero.
        </ResetCard>
        <ResetCard num={3} title="Drawdown Lock (requires human review)">
          If the portfolio drops 15% from its peak, the bot writes a <code className="font-mono">HALTED.lock</code> file
          and stops completely. To resume trading, a human must review what caused the drawdown,
          then reset via one of two options:
          <div className="mt-2 text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>Option 1 — Dashboard</div>
          <ul className="mt-1 space-y-1 text-xs">
            <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Click the <strong>Reset Circuit Breaker</strong> button in the dashboard header or at the top of this page (visible when status is RED or HALTED)</li>
            <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> The lock file and state file are deleted; peak equity is re-seeded on the next cycle</li>
          </ul>
          <div className="mt-2 text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>Option 2 — Server (if dashboard is inaccessible)</div>
          <ul className="mt-1 space-y-1 text-xs">
            <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Open a Render Shell and delete <code className="font-mono">data/HALTED.lock</code> manually</li>
            <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Restart or allow the next scheduler cycle to run</li>
          </ul>
        </ResetCard>
      </div>

      <div
        className="mt-4 p-3 rounded text-xs"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
          color: 'var(--text-secondary)',
          borderLeft: '3px solid var(--accent)',
        }}
      >
        The drawdown peak tracks the highest portfolio value ever recorded. After a loss, the peak
        does not drop — only new highs update it. This means a portfolio that recovers from a 10%
        drawdown and then drops again will trigger the threshold again relative to the same (or new) peak.
      </div>

      <div
        className="mt-3 p-3 rounded text-xs"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
          color: 'var(--text-secondary)',
          borderLeft: '3px solid var(--accent)',
        }}
      >
        <strong>Stale peak guard:</strong> If the recorded peak is more than 50% above the current
        equity — implying a drawdown so severe that the 15% lock would have already fired — the
        peak is almost certainly stale from a previous session or account reset. Rather than
        triggering a false HALTED, the bot re-seeds the peak to the current equity and logs a
        warning. This protects against corrupt state data without silently masking real drawdowns.
      </div>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
