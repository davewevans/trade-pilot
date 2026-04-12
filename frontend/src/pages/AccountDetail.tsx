import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ACCOUNTS, api } from '../api/client'
import { useAccount } from '../hooks/useAccount'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import { StatCard } from '../components/shared/StatCard'
import type { Position, Trade } from '../types'

// Maps the URL account slug to the strategy_type tag that
// StateWriter.write_portfolio_snapshot stamps on each position. Keep in
// sync with `_tag_strategy_type` in data/state_writer.py.
const ACCOUNT_TO_POSITION_TAG: Record<string, string> = {
  wheel: 'wheel',
  spreads: 'spread',
  iron_condor: 'iron_condor',
}

const fmtMoney = (n: number | null | undefined) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`

const fmtSigned = (n: number) =>
  `${n >= 0 ? '+' : ''}$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`

const _WHEEL_PHASES = ['IDLE', 'SHORT_PUT', 'LONG_STOCK', 'SHORT_CALL'] as const
type WheelPhase = (typeof _WHEEL_PHASES)[number]

function WheelPipeline({ states }: { states: Record<string, string> }) {
  const entries = Object.entries(states)
  if (!entries.length) return <EmptyState message="No wheel state yet" />
  return (
    <div className="space-y-2">
      {entries.map(([sym, state]) => (
        <div key={sym} className="flex items-center gap-3 text-xs">
          <div className="w-16 font-mono">{sym}</div>
          <div className="flex items-center gap-1">
            {_WHEEL_PHASES.map((p, idx) => {
              const active = (state as WheelPhase) === p
              return (
                <span key={p} className="flex items-center gap-1">
                  <span
                    className="px-2 py-0.5 rounded text-[10px] uppercase tracking-wider"
                    style={{
                      color: active ? 'var(--bg-primary)' : 'var(--text-muted)',
                      backgroundColor: active ? 'var(--accent)' : 'transparent',
                      border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
                      fontWeight: active ? 700 : 500,
                    }}
                  >
                    {p}
                  </span>
                  {idx < _WHEEL_PHASES.length - 1 && (
                    <span style={{ color: 'var(--text-muted)' }}>→</span>
                  )}
                </span>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

function PositionRow({ p }: { p: Position }) {
  const pl = Number(p.unrealized_pnl ?? 0)
  return (
    <tr>
      <td className="font-mono text-xs">{p.symbol}</td>
      <td><Badge variant="neutral">{p.strategy_type}</Badge></td>
      <td className="font-mono tabular">{p.strike ?? '—'}</td>
      <td className="font-mono text-xs">{p.expiration ?? '—'}</td>
      <td className="font-mono tabular">{p.dte ?? '—'}</td>
      <td className="font-mono tabular">{fmtMoney(p.entry_credit)}</td>
      <td className="font-mono tabular">{fmtMoney(p.current_value)}</td>
      <td
        className="font-mono tabular"
        style={{ color: pl >= 0 ? 'var(--green)' : 'var(--red)' }}
      >
        {fmtSigned(pl)}
      </td>
      <td className="font-mono tabular text-xs" style={{ color: 'var(--text-muted)' }}>
        {p.delta != null ? p.delta.toFixed(2) : '—'}
      </td>
    </tr>
  )
}

export function AccountDetail() {
  const { account = '' } = useParams()
  const accountMeta = ACCOUNTS.find((a) => a.account === account)
  const { portfolio, stats, performance, loading } = useAccount(account)

  const [trades, setTrades] = useState<Trade[] | null>(null)
  const [tradesLoading, setTradesLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setTradesLoading(true)
    api
      .trades({ account, limit: 100 })
      .then((r) => {
        if (cancelled) return
        // API returns most recent first; keep that ordering.
        setTrades(r.trades)
      })
      .catch(() => {
        if (!cancelled) setTrades([])
      })
      .finally(() => {
        if (!cancelled) setTradesLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [account])

  if (!accountMeta) {
    return (
      <EmptyState
        message={`Unknown account: ${account}`}
        hint="Pick one from the sidebar."
      />
    )
  }

  if (loading && !portfolio) return <LoadingSpinner />

  const positionTag = ACCOUNT_TO_POSITION_TAG[account]
  const positions = (portfolio?.positions ?? []).filter(
    (p) => p.strategy_type === positionTag,
  )

  const wheelStates = portfolio?.wheel_states ?? {}
  const isWheel = account === 'wheel'

  // Last 90 days of equity curve
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - 90)
  const equityData = (performance?.equity_curve ?? []).filter(
    (p) => new Date(p.date) >= cutoff,
  )

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <div>
          <Link to="/" className="text-xs" style={{ color: 'var(--text-muted)' }}>
            ← Dashboard
          </Link>
          <h2 className="text-2xl font-semibold mt-1">{accountMeta.label}</h2>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard
          label="Portfolio value"
          value={fmtMoney(portfolio?.account?.total_equity)}
          size="lg"
        />
        <StatCard
          label="Buying power"
          value={fmtMoney(portfolio?.account?.buying_power)}
          size="lg"
        />
        <StatCard
          label="Open positions"
          value={positions.length}
          size="lg"
        />
        <StatCard
          label="Win rate"
          value={stats?.win_rate != null ? `${stats.win_rate.toFixed(1)}%` : '—'}
          sub={stats ? `${stats.trades} trades / ${stats.skips} skips` : undefined}
          size="lg"
        />
      </div>

      {isWheel && (
        <section>
          <h3 className="text-sm font-semibold mb-3">Wheel state</h3>
          <div
            className="rounded p-4"
            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
          >
            <WheelPipeline states={wheelStates} />
          </div>
        </section>
      )}

      <section>
        <h3 className="text-sm font-semibold mb-3">Open positions</h3>
        <div
          className="rounded overflow-hidden"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          {positions.length === 0 ? (
            <EmptyState message="No open positions" />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Type</th>
                  <th>Strike</th>
                  <th>Expiry</th>
                  <th>DTE</th>
                  <th>Entry</th>
                  <th>Current</th>
                  <th>P&amp;L</th>
                  <th>Delta</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => <PositionRow key={p.symbol} p={p} />)}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold mb-3">Equity curve (last 90 days)</h3>
        <div
          className="rounded p-3"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', height: 280 }}
        >
          {equityData.length === 0 ? (
            <EmptyState message="No equity data yet" />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={equityData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="var(--text-muted)" fontSize={11} />
                <YAxis stroke="var(--text-muted)" fontSize={11} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'var(--bg-secondary)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                  }}
                />
                <ReferenceLine y={0} stroke="var(--text-muted)" />
                <Line
                  type="monotone"
                  dataKey="cumulative_pnl"
                  stroke="var(--accent)"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold mb-3">Closed trades</h3>
        <div
          className="rounded overflow-hidden"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          {tradesLoading && trades === null ? (
            <LoadingSpinner />
          ) : !trades || trades.length === 0 ? (
            <EmptyState message="No closed trades yet" />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Underlying</th>
                  <th>Type</th>
                  <th>Symbol</th>
                  <th>Fill price</th>
                  <th>P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <ClosedTradeRow key={`${t.id ?? i}-${t.symbol}`} t={t} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  )
}

function ClosedTradeRow({ t }: { t: Trade }) {
  const pl = Number(t.pnl ?? 0)
  return (
    <tr>
      <td className="font-mono text-xs" style={{ color: 'var(--text-muted)' }}>
        {t.filled_at ? new Date(t.filled_at).toLocaleString() : '—'}
      </td>
      <td className="font-mono">{t.underlying}</td>
      <td><Badge variant="action">{t.trade_type}</Badge></td>
      <td className="font-mono text-xs">{t.symbol}</td>
      <td className="font-mono tabular">{fmtMoney(t.fill_price)}</td>
      <td
        className="font-mono tabular"
        style={{ color: pl >= 0 ? 'var(--green)' : 'var(--red)' }}
      >
        {fmtSigned(pl)}
      </td>
    </tr>
  )
}
