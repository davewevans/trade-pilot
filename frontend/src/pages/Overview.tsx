/**
 * Mobile-first single-screen bot status overview.
 *
 * Step 4 decisions:
 *   - /api/heartbeat added to server.py (reads data/heartbeat.json, returns ts/job/elapsed_s/stale_minutes).
 *   - "Next job" is computed client-side from WEEKDAY_SCHEDULE below.
 *     Sync with config.py SCHEDULE if job times change.
 *
 * Step 3a position classifications (all active — entry_credit, current_value,
 * unrealized_pnl, dte are all present in the Position type):
 *   - near expiry:        dte <= 7
 *   - near profit target: entry_credit > 0 AND unrealized_pnl / entry_credit >= 0.5
 *   - at risk (credit):  entry_credit > 0 AND unrealized_pnl / entry_credit <= -1.0
 *   - at risk (debit):   entry_credit < 0 AND unrealized_pnl <= 0.5 * entry_credit
 */
import { Link } from 'react-router-dom'
import { useOverviewData } from '../hooks/useOverviewData'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import type { Position, Decision } from '../types'

// ── Schedule (Sync with config.py SCHEDULE — weekday ET times) ───────────────
const WEEKDAY_SCHEDULE: { job: string; time: string; label: string }[] = [
  { job: 'pre_market',     time: '06:00', label: 'Pre-market' },
  { job: 'market_open',    time: '10:00', label: 'Entry evaluation' },
  { job: 'position_check', time: '10:45', label: 'Position check' },
  { job: 'position_check', time: '11:30', label: 'Position check' },
  { job: 'position_check', time: '12:30', label: 'Position check' },
  { job: 'position_check', time: '14:00', label: 'Position check' },
  { job: 'expiry_guard',   time: '15:00', label: 'Expiry guard' },
  { job: 'pre_close',      time: '15:15', label: 'Pre-close' },
  { job: 'market_close',   time: '16:00', label: 'Market close' },
  { job: 'post_market',    time: '16:30', label: 'Post-market' },
]

const ACCOUNT_ACCENT: Record<string, string> = {
  wheel: 'var(--accent-wheel)',
  iron_condor: 'var(--accent-iron-condor)',
  spreads: 'var(--accent-spreads)',
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const fmtMoney = (n: number | null | undefined) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`

function todayInET(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date())
}

function isToday(isoTs: string): boolean {
  try {
    const d = new Date(isoTs)
    const dateStr = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(d)
    return dateStr === todayInET()
  } catch {
    return false
  }
}

function fmtTimeET(isoTs: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    }).format(new Date(isoTs))
  } catch {
    return '—'
  }
}

function fmtMinutesAgo(isoTs: string): string {
  const ms = Date.now() - new Date(isoTs).getTime()
  const mins = Math.round(ms / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.floor(mins / 60)
  return `${hrs}h ${mins % 60}m ago`
}

function fmtUpdatedTime(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** Compute ET time-of-day as minutes since midnight, for schedule comparison. */
function nowETMinutes(): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date())
  const h = parseInt(parts.find((p) => p.type === 'hour')?.value ?? '0', 10)
  const m = parseInt(parts.find((p) => p.type === 'minute')?.value ?? '0', 10)
  return h * 60 + m
}

function scheduleTimeMinutes(time: string): number {
  const [h, m] = time.split(':').map(Number)
  return h * 60 + m
}

function nextScheduledJob(): { label: string; time: string; inMinutes: number } | null {
  const nowMins = nowETMinutes()
  for (const entry of WEEKDAY_SCHEDULE) {
    const entryMins = scheduleTimeMinutes(entry.time)
    if (entryMins > nowMins) {
      return { label: entry.label, time: entry.time, inMinutes: entryMins - nowMins }
    }
  }
  // Tomorrow's first job
  const first = WEEKDAY_SCHEDULE[0]
  const minsUntilMidnight = 24 * 60 - nowMins
  return {
    label: first.label,
    time: first.time,
    inMinutes: minsUntilMidnight + scheduleTimeMinutes(first.time),
  }
}

function fmtInMinutes(mins: number): string {
  if (mins < 1) return 'now'
  if (mins < 60) return `in ${Math.round(mins)} min`
  const h = Math.floor(mins / 60)
  const m = Math.round(mins % 60)
  return m > 0 ? `in ${h}h ${m}m` : `in ${h}h`
}

// ── Position classification ──────────────────────────────────────────────────

type AttentionReason = 'near_expiry' | 'near_profit_target' | 'at_risk'

interface AttentionPosition {
  position: Position
  account: string
  reasons: AttentionReason[]
}

function classifyPositions(
  portfolios: Record<string, import('../types').Portfolio | null>,
): AttentionPosition[] {
  const result: AttentionPosition[] = []
  for (const [account, portfolio] of Object.entries(portfolios)) {
    if (!portfolio?.positions) continue
    for (const pos of portfolio.positions) {
      const reasons: AttentionReason[] = []
      if (pos.dte != null && pos.dte <= 7) reasons.push('near_expiry')
      if (pos.entry_credit != null && pos.unrealized_pnl != null) {
        if (pos.entry_credit > 0) {
          const pct = pos.unrealized_pnl / pos.entry_credit
          if (pct >= 0.5) reasons.push('near_profit_target')
          if (pct <= -1.0) reasons.push('at_risk')
        } else if (pos.entry_credit < 0) {
          if (pos.unrealized_pnl <= 0.5 * pos.entry_credit) reasons.push('at_risk')
        }
      }
      if (reasons.length > 0) result.push({ position: pos, account, reasons })
    }
  }
  return result
}

const REASON_LABELS: Record<AttentionReason, string> = {
  near_expiry: 'DTE',
  near_profit_target: 'PROFIT TARGET',
  at_risk: 'AT RISK',
}

const REASON_COLORS: Record<AttentionReason, string> = {
  near_expiry: 'var(--yellow, #eab308)',
  near_profit_target: 'var(--green)',
  at_risk: 'var(--red)',
}

// ── Card shell ────────────────────────────────────────────────────────────────

function Card({
  children,
  leftBorder,
}: {
  children: React.ReactNode
  leftBorder?: string
}) {
  return (
    <div
      className="rounded-md p-4"
      style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderLeft: leftBorder ? `4px solid ${leftBorder}` : '1px solid var(--border)',
      }}
    >
      {children}
    </div>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2
      className="text-xs uppercase tracking-wider font-semibold mb-2 mt-5 first:mt-0"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h2>
  )
}

// ── Status strip ─────────────────────────────────────────────────────────────

function StatusStrip({
  halted,
  dryRun,
  cbStatus,
}: {
  halted: boolean
  dryRun: boolean
  cbStatus: string | null
}) {
  const dotColor = halted ? 'var(--red)' : cbStatus === 'YELLOW' ? 'var(--yellow, #eab308)' : 'var(--green)'
  const label = halted ? 'HALTED' : dryRun ? 'DRY RUN' : cbStatus === 'YELLOW' ? 'CAUTION' : 'Running'

  return (
    <div
      className="flex items-center gap-3 rounded-md px-4 py-3"
      style={{
        backgroundColor: halted
          ? 'color-mix(in srgb, var(--red) 12%, var(--bg-card))'
          : 'var(--bg-card)',
        border: `1px solid ${halted ? 'var(--red)' : 'var(--border)'}`,
        minHeight: 48,
      }}
    >
      <span
        className="rounded-full shrink-0"
        style={{ width: 14, height: 14, backgroundColor: dotColor, display: 'inline-block' }}
      />
      <span className="font-semibold text-base" style={{ color: halted ? 'var(--red)' : 'var(--text-primary)' }}>
        {label}
      </span>
      {dryRun && !halted && (
        <span
          className="text-xs px-2 py-0.5 rounded"
          style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}
        >
          dry run
        </span>
      )}
    </div>
  )
}

// ── Circuit breaker card ──────────────────────────────────────────────────────

function CircuitBreakerCard({
  status,
  reason,
  halted,
}: {
  status: string | null
  reason?: string | null
  halted: boolean
}) {
  const isRed = halted || status === 'RED'
  return (
    <Card leftBorder={isRed ? 'var(--red)' : status === 'YELLOW' ? 'var(--yellow, #eab308)' : undefined}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>
          Circuit breaker
        </span>
        {status && <Badge variant="circuit" value={status}>{status}</Badge>}
      </div>
      {reason && (
        <p className="text-sm mt-1" style={{ color: isRed ? 'var(--red)' : 'var(--text-secondary)' }}>
          {reason}
        </p>
      )}
      {!status && (
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>No data</p>
      )}
    </Card>
  )
}

// ── Regime & IV strip ─────────────────────────────────────────────────────────

function RegimePill({ label, value, danger }: { label: string; value: string | null; danger?: boolean }) {
  return (
    <div
      className="flex-1 rounded px-3 py-2 text-center"
      style={{
        backgroundColor: danger ? 'color-mix(in srgb, var(--red) 15%, var(--bg-card))' : 'var(--bg-secondary)',
        border: `1px solid ${danger ? 'var(--red)' : 'var(--border)'}`,
        minHeight: 52,
      }}
    >
      <div className="text-[11px] uppercase tracking-wide mb-1" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="text-sm font-semibold" style={{ color: danger ? 'var(--red)' : 'var(--text-primary)' }}>
        {value ?? '—'}
      </div>
    </div>
  )
}

// ── Cycle card ────────────────────────────────────────────────────────────────

function CycleCard({
  heartbeat,
}: {
  heartbeat: import('../api/client').HeartbeatResponse | null
}) {
  const next = nextScheduledJob()
  const isWeekend = [0, 6].includes(new Date().getDay())

  const lastLine = heartbeat?.ts
    ? `${heartbeat.job ?? '?'} at ${fmtTimeET(heartbeat.ts)} ET (${fmtMinutesAgo(heartbeat.ts)})`
    : 'No data yet'

  const stale = heartbeat?.stale_minutes != null && heartbeat.stale_minutes >= 15 && !isWeekend

  const nextLine = next
    ? `${next.label} at ${next.time} ET (${fmtInMinutes(next.inMinutes)})`
    : '—'

  return (
    <Card>
      <div className="text-sm font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>
        Schedule
      </div>
      <div className="space-y-2 text-sm">
        <div className="flex items-start gap-2">
          <span style={{ color: 'var(--text-muted)', minWidth: 64, flexShrink: 0 }}>Last</span>
          <span style={{ color: 'var(--text-primary)' }}>
            {lastLine}
            {stale && (
              <span
                className="ml-2 text-xs px-1.5 py-0.5 rounded"
                style={{ backgroundColor: 'color-mix(in srgb, var(--yellow, #eab308) 20%, transparent)', color: 'var(--yellow, #eab308)', border: '1px solid var(--yellow, #eab308)' }}
              >
                stale
              </span>
            )}
          </span>
        </div>
        <div className="flex items-start gap-2">
          <span style={{ color: 'var(--text-muted)', minWidth: 64, flexShrink: 0 }}>Next</span>
          <span style={{ color: 'var(--text-primary)' }}>{isWeekend ? 'Weekend — no jobs' : nextLine}</span>
        </div>
      </div>
    </Card>
  )
}

// ── Account card ─────────────────────────────────────────────────────────────

function AccountCard({
  accountId,
  label,
  strategyName,
  portfolio,
}: {
  accountId: string
  label: string
  strategyName: string | null
  portfolio: import('../types').Portfolio | null
}) {
  const accent = ACCOUNT_ACCENT[accountId] ?? 'var(--accent)'
  const equity = portfolio?.account?.total_equity
  const todayPnl = portfolio?.account?.today_pnl
  const posCount = portfolio?.positions?.length ?? 0

  const todayColor =
    todayPnl == null || todayPnl === 0
      ? 'var(--text-muted)'
      : todayPnl > 0
        ? 'var(--green)'
        : 'var(--red)'

  const todayDisplay =
    todayPnl == null || todayPnl === 0
      ? '—'
      : `${todayPnl > 0 ? '+' : ''}${fmtMoney(todayPnl)}`

  return (
    <Link
      to={`/account/${accountId}`}
      className="block rounded-md p-4 transition-opacity hover:opacity-90"
      style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderTop: `3px solid ${accent}`,
        minHeight: 44,
      }}
    >
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{label}</h3>
      </div>
      {strategyName && (
        <div className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>{strategyName}</div>
      )}
      <div className="text-2xl font-mono tabular mb-1" style={{ color: 'var(--text-primary)' }}>
        {fmtMoney(equity)}
      </div>
      <div className="flex gap-4 text-xs mt-2">
        <div>
          <span style={{ color: 'var(--text-muted)' }}>Positions </span>
          <span className="font-mono" style={{ color: 'var(--text-primary)' }}>{posCount}</span>
        </div>
        <div>
          <span style={{ color: 'var(--text-muted)' }}>Today </span>
          <span className="font-mono" style={{ color: todayColor }}>{todayDisplay}</span>
        </div>
      </div>
    </Link>
  )
}

// ── Entry row ─────────────────────────────────────────────────────────────────

function EntryRow({ d }: { d: Decision }) {
  const ts = d.timestamp || d.created_at || ''
  return (
    <div
      className="flex items-center gap-3 py-2.5"
      style={{ borderBottom: '1px solid var(--border)', minHeight: 44 }}
    >
      <span className="font-mono text-xs shrink-0" style={{ color: 'var(--text-muted)', minWidth: 52 }}>
        {ts ? fmtTimeET(ts) : '—'}
      </span>
      <span className="font-mono text-sm font-semibold" style={{ color: 'var(--text-primary)', minWidth: 60 }}>
        {d.underlying ?? '—'}
      </span>
      <Badge variant="action">{d.action}</Badge>
      {d.strategy_type && (
        <span className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>{d.strategy_type}</span>
      )}
    </div>
  )
}

// ── Attention row ─────────────────────────────────────────────────────────────

function AttentionRow({ item }: { item: AttentionPosition }) {
  const pos = item.position
  return (
    <div
      className="flex items-center gap-3 py-2.5"
      style={{ borderBottom: '1px solid var(--border)', minHeight: 44 }}
    >
      <span className="font-mono text-sm font-semibold" style={{ color: 'var(--text-primary)', minWidth: 72 }}>
        {pos.underlying ?? pos.symbol}
      </span>
      <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)' }}>
        DTE {pos.dte}
      </span>
      <div className="flex gap-1 flex-wrap">
        {item.reasons.map((r) => (
          <span
            key={r}
            className="text-xs px-1.5 py-0.5 rounded font-semibold"
            style={{
              backgroundColor: `color-mix(in srgb, ${REASON_COLORS[r]} 15%, transparent)`,
              color: REASON_COLORS[r],
              border: `1px solid ${REASON_COLORS[r]}`,
            }}
          >
            {r === 'near_expiry' ? `DTE ${pos.dte}` : REASON_LABELS[r]}
          </span>
        ))}
      </div>
      <span className="text-xs ml-auto shrink-0" style={{ color: 'var(--text-muted)' }}>
        {item.account}
      </span>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Overview() {
  const { circuitBreaker, haltInfo, context, accounts, portfolios, recentDecisions, skipDecisions, heartbeat, loading, lastUpdated } =
    useOverviewData()

  const dryRun = Boolean(circuitBreaker?.dry_run)
  const halted = Boolean(haltInfo?.halted) && !dryRun
  const cbStatus = circuitBreaker?.status ?? null

  const regime = context?.confirmed_market_regime as string | null | undefined
  const ivEnv = (context as Record<string, unknown> | null)?.iv_environment as string | null | undefined

  const activeAccounts = accounts.filter((a) => a.status === 'active')

  // Today's non-SKIP entries
  const todaysEntries = recentDecisions
    .filter((d) => {
      const ts = d.timestamp || d.created_at || ''
      return d.action !== 'SKIP' && ts && isToday(ts)
    })
    .sort((a, b) => {
      const ta = a.timestamp || a.created_at || ''
      const tb = b.timestamp || b.created_at || ''
      return new Date(tb).getTime() - new Date(ta).getTime()
    })
    .slice(0, 5)

  // Positions needing attention
  const attentionPositions = classifyPositions(portfolios).slice(0, 10)

  // Open positions summary by strategy
  const positionsByStrategy: Record<string, number> = {}
  for (const portfolio of Object.values(portfolios)) {
    if (!portfolio?.positions) continue
    for (const pos of portfolio.positions) {
      const s = pos.strategy_type ?? 'unknown'
      positionsByStrategy[s] = (positionsByStrategy[s] ?? 0) + 1
    }
  }
  const strategiesWithPositions = Object.entries(positionsByStrategy).filter(([, n]) => n > 0)

  // Skip reasons today
  const todaysSkips = skipDecisions.filter((d) => {
    const ts = d.timestamp || d.created_at || ''
    return ts && isToday(ts)
  })
  const skipCounts: Record<string, number> = {}
  for (const d of todaysSkips) {
    const dRec = d as unknown as Record<string, unknown>
    const reason = (dRec.skip_reason as string | null) ?? (dRec.skip_code as string | null) ?? 'UNKNOWN'
    skipCounts[reason] = (skipCounts[reason] ?? 0) + 1
  }
  const topSkips = Object.entries(skipCounts)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 3)

  if (loading) {
    return (
      <div
        className="min-h-screen flex items-center justify-center"
        style={{ backgroundColor: 'var(--bg-primary)' }}
      >
        <LoadingSpinner />
      </div>
    )
  }

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--bg-primary)' }}>
      {/* Page header */}
      <div
        className="sticky top-0 z-10 flex items-center justify-between px-4 py-3 border-b"
        style={{
          backgroundColor: 'var(--bg-secondary)',
          borderColor: 'var(--border)',
        }}
      >
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
            trade-pilot
          </span>
          <span
            className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
            style={{
              backgroundColor: 'color-mix(in srgb, var(--accent) 15%, var(--bg-card))',
              color: 'var(--accent)',
              border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
            }}
          >
            Mobile
          </span>
        </div>
        <a
          href="/"
          className="text-xs"
          style={{ color: 'var(--text-muted)' }}
        >
          Full dashboard
        </a>
      </div>

    <div className="max-w-2xl mx-auto px-4 py-4 space-y-0">
      {/* 1 — Status strip */}
      <StatusStrip halted={halted} dryRun={dryRun} cbStatus={cbStatus} />

      {/* 2 — Circuit breaker card */}
      <div className="mt-4">
        <CircuitBreakerCard
          status={cbStatus}
          reason={(circuitBreaker as Record<string, unknown> | null)?.reason as string | null}
          halted={halted}
        />
      </div>

      {/* 3 — Regime & IV strip */}
      <div className="mt-4 flex gap-3">
        <RegimePill
          label="Market regime"
          value={regime ?? null}
          danger={regime === 'CRASH'}
        />
        <RegimePill
          label="IV environment"
          value={ivEnv ?? null}
        />
      </div>

      {/* 4 — Schedule / cycle card */}
      <div className="mt-4">
        <CycleCard heartbeat={heartbeat} />
      </div>

      {/* 5 — Per-account cards */}
      <SectionHeading>Accounts</SectionHeading>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {activeAccounts.map((a) => (
          <AccountCard
            key={a.account_id}
            accountId={a.account_id}
            label={a.label}
            strategyName={a.strategy_display_name}
            portfolio={portfolios[a.account_id] ?? null}
          />
        ))}
        {activeAccounts.length === 0 && (
          <p className="text-sm col-span-full" style={{ color: 'var(--text-muted)' }}>No active accounts</p>
        )}
      </div>

      {/* 6 — Today's new entries */}
      <SectionHeading>Today&apos;s entries</SectionHeading>
      <div
        className="rounded-md overflow-hidden"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {todaysEntries.length === 0 ? (
          <div className="p-4">
            <EmptyState icon="clock" message="No entries yet today" />
          </div>
        ) : (
          <Link to="/decisions" className="block" style={{ color: 'inherit', textDecoration: 'none' }}>
            <div className="px-4">
              {todaysEntries.map((d) => (
                <EntryRow key={d.id} d={d} />
              ))}
            </div>
            <div className="px-4 py-2 text-xs" style={{ color: 'var(--accent)' }}>
              View all decisions
            </div>
          </Link>
        )}
      </div>

      {/* 7 — Positions needing attention */}
      <SectionHeading>Positions needing attention</SectionHeading>
      <div
        className="rounded-md overflow-hidden"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {attentionPositions.length === 0 ? (
          <div className="p-4">
            <EmptyState icon="inbox" message="All positions healthy" />
          </div>
        ) : (
          <div className="px-4">
            {attentionPositions.map((item, i) => (
              <AttentionRow key={`${item.account}-${item.position.symbol}-${i}`} item={item} />
            ))}
          </div>
        )}
      </div>

      {/* 8 — Open positions summary */}
      {strategiesWithPositions.length > 0 && (
        <>
          <SectionHeading>Open positions</SectionHeading>
          <div
            className="rounded-md px-4 py-3 flex flex-wrap gap-x-4 gap-y-1 text-sm"
            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
          >
            {strategiesWithPositions.map(([strategy, count]) => (
              <span key={strategy} style={{ color: 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--text-muted)' }}>{strategy.replace(/_/g, ' ')}: </span>
                <span className="font-mono font-semibold" style={{ color: 'var(--text-primary)' }}>{count}</span>
              </span>
            ))}
          </div>
        </>
      )}

      {/* 9 — Skip reasons today (omit if none) */}
      {topSkips.length > 0 && (
        <>
          <SectionHeading>Skip reasons today</SectionHeading>
          <div
            className="rounded-md px-4 py-3 flex flex-wrap gap-x-4 gap-y-1 text-sm"
            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
          >
            {topSkips.map(([reason, count]) => (
              <span key={reason} style={{ color: 'var(--text-secondary)' }}>
                <span className="font-mono" style={{ color: 'var(--text-muted)' }}>{reason}: </span>
                <span className="font-mono font-semibold" style={{ color: 'var(--text-primary)' }}>{count}</span>
              </span>
            ))}
          </div>
        </>
      )}

      {/* Footer */}
      <div className="pt-4 pb-6 text-xs text-center" style={{ color: 'var(--text-muted)' }}>
        {lastUpdated
          ? `Updated ${fmtUpdatedTime(lastUpdated)} · auto-refresh 30s`
          : 'Loading…'}
      </div>
    </div>
    </div>
  )
}
