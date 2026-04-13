import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ACCOUNTS, api, type ContextResponse } from '../api/client'
import { useAccount } from '../hooks/useAccount'
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
  cbStatus,
  snapshot,
}: {
  account: string
  label: string
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

export function Dashboard() {
  const { data, loading } = useDecisions({ limit: 20 })
  const [context, setContext] = useState<ContextResponse | null>(null)
  const [cb, setCb] = useState<CircuitBreaker | null>(null)
  const [accounts, setAccounts] = useState<Record<string, Portfolio | null>>({})
  const [watchlistCounts, setWatchlistCounts] = useState<{ wheel: number; spreads: number } | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.context().then((c) => { if (!cancelled) setContext(c) }).catch(() => {
        if (!cancelled) setContext(null)
      })
      api.circuitBreakers().then((c) => { if (!cancelled) setCb(c) }).catch(() => {
        if (!cancelled) setCb(null)
      })
      api.accounts().then((a) => { if (!cancelled) setAccounts(a) }).catch(() => {
        if (!cancelled) setAccounts({})
      })
      api.watchlist().then((w) => {
        if (!cancelled) setWatchlistCounts({ wheel: w.wheel.length, spreads: w.spreads.length })
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
          {ACCOUNTS.map((a) => (
            <AccountCard
              key={a.account}
              account={a.account}
              label={a.label}
              cbStatus={cbStatus}
              snapshot={accounts[a.account] ?? null}
            />
          ))}
        </div>
        {watchlistCounts && (
          <p className="mt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
            Watching{' '}
            <span style={{ color: 'var(--text-secondary)' }}>
              {watchlistCounts.wheel} stocks
            </span>{' '}
            for wheel,{' '}
            <span style={{ color: 'var(--text-secondary)' }}>
              {watchlistCounts.spreads} names
            </span>{' '}
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
