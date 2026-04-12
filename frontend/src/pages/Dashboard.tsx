import { Link } from 'react-router-dom'
import { ACCOUNTS } from '../api/client'
import { useAccount } from '../hooks/useAccount'
import { useDecisions } from '../hooks/useDecisions'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import type { Decision, Performance } from '../types'

const fmtMoney = (n: number | null | undefined) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`

function todayPnl(perf: Performance | null): number | null {
  if (!perf || !perf.equity_curve.length) return null
  const today = new Date().toISOString().slice(0, 10)
  const idx = perf.equity_curve.findIndex((p) => p.date === today)
  if (idx < 0) return null
  const prev = idx > 0 ? perf.equity_curve[idx - 1].cumulative_pnl : 0
  return perf.equity_curve[idx].cumulative_pnl - prev
}

function AccountCard({ account, label }: { account: string; label: string }) {
  const { portfolio, stats, performance, loading } = useAccount(account)
  const todays = todayPnl(performance)

  const equity = portfolio?.account?.total_equity
  const bp = portfolio?.account?.buying_power
  const positionsCount = portfolio?.positions?.length ?? 0

  return (
    <Link
      to={`/account/${account}`}
      className="block rounded-md p-5 transition-colors hover:opacity-90"
      style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border)',
      }}
    >
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-base font-semibold">{label}</h3>
        {loading && <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>loading…</span>}
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
          <div
            className="font-mono tabular text-sm"
            style={{
              color: todays == null ? 'var(--text-muted)'
                : todays >= 0 ? 'var(--green)' : 'var(--red)',
            }}
          >
            {todays == null ? '—' : `${todays >= 0 ? '+' : ''}${fmtMoney(todays)}`}
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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-3">Accounts</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {ACCOUNTS.map((a) => (
            <AccountCard key={a.account} account={a.account} label={a.label} />
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-3">Recent activity</h2>
        <div
          className="rounded overflow-hidden"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          {loading ? (
            <LoadingSpinner />
          ) : !data?.decisions.length ? (
            <EmptyState message="No decisions yet" hint="The bot will populate this on its next run." />
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
