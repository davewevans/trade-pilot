import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ContextResponse, type SourceHealthEntry } from '../api/client'
import { useAccount } from '../hooks/useAccount'
import { useAccounts } from '../hooks/useAccounts'
import { useDecisions } from '../hooks/useDecisions'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import { StatCard } from '../components/shared/StatCard'
import type { CircuitBreaker, Decision, Portfolio } from '../types'

const fmtMoney = (n: number | null | undefined) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`

const ACCOUNT_ACCENT: Record<string, string> = {
  wheel: 'var(--accent-wheel)',
  iron_condor: 'var(--accent-iron-condor)',
  spreads: 'var(--accent-spreads)',
}

function AccountCard({
  account,
  label,
  strategyName,
  cbStatus,
  snapshot,
}: {
  account: string
  label: string
  strategyName: string | null
  cbStatus: string | null
  snapshot: Portfolio | null
}) {
  const accent = ACCOUNT_ACCENT[account] ?? 'var(--accent)'
  const { stats } = useAccount(account)

  const equity = snapshot?.account?.total_equity
  const bp = snapshot?.account?.buying_power
  const todayPnlVal = snapshot?.account?.today_pnl
  const positionsCount = snapshot?.positions?.length ?? 0

  const todayColor =
    todayPnlVal == null || todayPnlVal === 0 ? 'var(--text-muted)'
      : todayPnlVal > 0 ? 'var(--green)' : 'var(--red)'
  const todayDisplay =
    todayPnlVal == null || todayPnlVal === 0
      ? '—'
      : `${todayPnlVal > 0 ? '+' : ''}${fmtMoney(todayPnlVal)}`

  return (
    <Link
      to={`/account/${account}`}
      className="block rounded-md p-5 transition-colors hover:opacity-90"
      style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderTop: `3px solid ${accent}`,
      }}
    >
      <div className="flex items-baseline justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-base font-semibold">{label}</h3>
          {cbStatus && <Badge variant="circuit">{cbStatus}</Badge>}
        </div>
      </div>
      {strategyName && (
        <div className="text-sm mb-2" style={{ color: 'var(--text-muted)' }}>
          {strategyName}
        </div>
      )}
      <div
        className="text-3xl font-mono tabular mb-1"
        style={{ color: 'var(--text-primary)' }}
      >
        {fmtMoney(equity)}
      </div>
      <div className="text-xs mb-4" style={{ color: 'var(--text-muted)' }}>
        Buying power: <span className="font-mono tabular">{fmtMoney(bp)}</span>
      </div>
      <div className="grid grid-cols-3 gap-3 text-xs">
        <div>
          <div style={{ color: 'var(--text-muted)' }}>Open</div>
          <div className="font-mono tabular text-sm" style={{ color: 'var(--text-primary)' }}>
            {positionsCount}
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--text-muted)' }}>Win rate</div>
          <div className="font-mono tabular text-sm" style={{ color: 'var(--text-primary)' }}>
            {stats?.win_rate != null ? `${stats.win_rate.toFixed(1)}%` : '—'}
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--text-muted)' }}>Today</div>
          <div className="font-mono tabular text-sm" style={{ color: todayColor }}>
            {todayDisplay}
          </div>
        </div>
      </div>
    </Link>
  )
}

function ActivityRow({ d }: { d: Decision }) {
  const ts = d.timestamp || d.created_at || ''
  const reasoningPreview = (() => {
    const r = d.reasoning
    if (!r) return ''
    if (typeof r === 'string') return r
    return (r.macro || r.selection || r.risk || '').slice(0, 80)
  })()
  return (
    <tr>
      <td className="font-mono tabular text-xs" style={{ color: 'var(--text-muted)' }}>
        {ts ? new Date(ts).toLocaleString() : '—'}
      </td>
      <td><Badge variant="neutral">{d.strategy_type}</Badge></td>
      <td className="font-mono">{d.underlying}</td>
      <td><Badge variant="action">{d.action}</Badge></td>
      <td><Badge variant="confidence" value={d.confidence ?? undefined} /></td>
      <td className="text-xs truncate max-w-[420px]" style={{ color: 'var(--text-secondary)' }}>
        {reasoningPreview}
      </td>
    </tr>
  )
}

// ── Data health panel ────────────────────────────────────────

const HEALTH_SOURCES = [
  'Alpaca', 'Alpaca News', 'ORATS', 'yfinance', 'CNN Fear & Greed', 'FRED', 'Finnhub',
]

type HealthStatus = 'green' | 'yellow' | 'red' | 'gray'

function healthStatus(entry: SourceHealthEntry | undefined): HealthStatus {
  if (!entry || entry.last_checked === null) return 'gray'
  const failures = entry.consecutive_failures
  if (failures >= 3) return 'red'
  const lastSuccess = entry.last_success ? new Date(entry.last_success).getTime() : null
  const ageMs = lastSuccess == null ? Infinity : Date.now() - lastSuccess
  const ageMins = ageMs / 60_000
  if (lastSuccess === null) return 'red'
  if (failures >= 1 && failures <= 2) return 'yellow'
  if (ageMins > 120) return 'red'
  if (ageMins > 30) return 'yellow'
  return 'green'
}

const STATUS_DOT: Record<HealthStatus, { color: string; label: string }> = {
  green: { color: '#3fb950', label: 'healthy' },
  yellow: { color: '#d29922', label: 'stale' },
  red: { color: '#f85149', label: 'down' },
  gray: { color: '#6e7681', label: 'no data' },
}

function fmtAge(iso: string | null): string {
  if (!iso) return 'never'
  const ms = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(ms / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function SourceDot({
  name,
  entry,
}: {
  name: string
  entry: SourceHealthEntry | undefined
}) {
  const status = healthStatus(entry)
  const dot = STATUS_DOT[status]
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 px-2 py-1 rounded text-xs transition-colors"
        style={{
          backgroundColor: open ? 'var(--bg-secondary)' : 'transparent',
          color: 'var(--text-secondary)',
          border: '1px solid transparent',
          cursor: 'pointer',
        }}
        title={`${name}: ${dot.label}`}
      >
        <span
          style={{
            display: 'inline-block',
            width: 8,
            height: 8,
            borderRadius: '50%',
            backgroundColor: dot.color,
            flexShrink: 0,
          }}
        />
        <span>{name}</span>
      </button>
      {open && entry && (
        <div
          className="absolute z-10 top-full mt-1 left-0 rounded-md p-3 text-xs space-y-1 min-w-[220px]"
          style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border)',
            boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
            color: 'var(--text-secondary)',
          }}
        >
          <div className="font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>
            {name}
          </div>
          <div>Last success: <span style={{ color: 'var(--text-primary)' }}>{fmtAge(entry.last_success)}</span></div>
          <div>Last failure: <span style={{ color: 'var(--text-primary)' }}>{fmtAge(entry.last_failure)}</span></div>
          {entry.last_failure_reason && (
            <div className="truncate max-w-[280px]">
              Reason: <span style={{ color: '#f85149' }}>{entry.last_failure_reason}</span>
            </div>
          )}
          <div>
            Today: <span style={{ color: '#3fb950' }}>{entry.today_successes} ok</span>
            {' / '}
            <span style={{ color: '#f85149' }}>{entry.today_failures} fail</span>
          </div>
          {entry.consecutive_failures > 0 && (
            <div style={{ color: '#f85149' }}>
              {entry.consecutive_failures} consecutive failure{entry.consecutive_failures !== 1 ? 's' : ''}
            </div>
          )}
        </div>
      )}
      {open && !entry && (
        <div
          className="absolute z-10 top-full mt-1 left-0 rounded-md p-3 text-xs"
          style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border)',
            color: 'var(--text-muted)',
          }}
        >
          No data recorded yet
        </div>
      )}
    </div>
  )
}

function DataHealthPanel() {
  const [sources, setSources] = useState<Record<string, SourceHealthEntry>>({})

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.sourceHealth().then((d) => {
        if (!cancelled) setSources(d.sources)
      }).catch(() => {})
    }
    load()
    const id = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  return (
    <div>
      <h2 className="section-heading">Data health</h2>
      <div
        className="rounded-md px-3 py-2"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <div className="flex flex-wrap gap-1">
          {HEALTH_SOURCES.map((name) => (
            <SourceDot key={name} name={name} entry={sources[name]} />
          ))}
        </div>
      </div>
    </div>
  )
}

export function Dashboard() {
  const { data, loading } = useDecisions({ limit: 20 })
  const { accounts: accountList } = useAccounts()
  const [context, setContext] = useState<ContextResponse | null>(null)
  const [cb, setCb] = useState<CircuitBreaker | null>(null)
  const [portfolios, setPortfolios] = useState<Record<string, Portfolio | null>>({})
  const [watchlistCounts, setWatchlistCounts] = useState<{ wheel: number; iron_condor: number; spreads: number } | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.context().then((c) => { if (!cancelled) setContext(c) }).catch(() => {
        if (!cancelled) setContext(null)
      })
      api.circuitBreakers().then((c) => { if (!cancelled) setCb(c) }).catch(() => {
        if (!cancelled) setCb(null)
      })
      api.accountPortfolios().then((a) => { if (!cancelled) setPortfolios(a) }).catch(() => {
        if (!cancelled) setPortfolios({})
      })
      api.watchlist().then((w) => {
        if (!cancelled) setWatchlistCounts({ wheel: w.wheel.length, iron_condor: w.iron_condor.length, spreads: w.spreads.length })
      }).catch(() => {})
    }
    load()
    const id = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const vix = context?.macro?.vix
  const fg = context?.macro?.fear_greed_score
  const fgRating = context?.macro?.fear_greed_rating
  const regime = context?.confirmed_market_regime
  const cbStatus = cb?.status ?? null

  return (
    <div className="space-y-6">
      <div>
        <h2 className="section-heading">Accounts</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {accountList.map((a) => (
            <AccountCard
              key={a.account_id}
              account={a.account_id}
              label={a.label}
              strategyName={a.strategy_display_name}
              cbStatus={cbStatus}
              snapshot={portfolios[a.account_id] ?? null}
            />
          ))}
        </div>
        {watchlistCounts && (
          <p className="mt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
            Watching{' '}
            <span style={{ color: 'var(--text-secondary)' }}>{watchlistCounts.wheel} stocks</span>{' '}
            for wheel,{' '}
            <span style={{ color: 'var(--text-secondary)' }}>{watchlistCounts.iron_condor} names</span>{' '}
            for iron condors,{' '}
            <span style={{ color: 'var(--text-secondary)' }}>{watchlistCounts.spreads} names</span>{' '}
            for spreads —{' '}
            <Link to="/watchlist" style={{ color: 'var(--accent)' }}>
              manage watchlist
            </Link>
          </p>
        )}
      </div>

      <div>
        <h2 className="section-heading">Market context</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <StatCard label="VIX" value={vix == null ? '—' : vix.toFixed(2)} />
          <StatCard
            label="Fear & Greed"
            value={fg == null ? '—' : fg}
            sub={fgRating || undefined}
          />
          <StatCard label="Market Regime" value={regime || '—'} />
        </div>
      </div>

      <DataHealthPanel />

      <div>
        <h2 className="section-heading">Recent activity</h2>
        <div
          className="rounded overflow-hidden"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          {loading ? (
            <LoadingSpinner />
          ) : !data?.decisions.length ? (
            <EmptyState
              icon="clock"
              message="No decisions yet"
              hint="The bot will fill this in on its next run — hang tight."
            />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Strategy</th>
                  <th>Underlying</th>
                  <th>Action</th>
                  <th>Confidence</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {data.decisions.map((d) => (
                  <ActivityRow key={d.id} d={d} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
