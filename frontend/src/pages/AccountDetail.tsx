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
import { ACCOUNTS } from '../api/client'
import { useAccount } from '../hooks/useAccount'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import { StatCard } from '../components/shared/StatCard'
import type { Position, Trade } from '../types'

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
  const pl = Number(p.unrealized_pl ?? 0)
  return (
    <tr>
      <td className="font-mono text-xs">{p.symbol}</td>
      <td><Badge variant="neutral">{p.strategy_type}</Badge></td>
      <td className="font-mono tabular">{p.strike_price ?? '—'}</td>
      <td className="font-mono text-xs">{p.expiration_date ?? '—'}</td>
      <td className="font-mono tabular">{p.dte ?? '—'}</td>
      <td className="font-mono tabular">{fmtMoney(p.avg_entry_price)}</td>
      <td className="font-mono tabular">{fmtMoney(p.current_price)}</td>
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

function TradeRow({ t }: { t: Trade }) {
  const pl = Number(t.pnl ?? 0)
  return (
    <tr>
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
      <td className="font-mono text-xs" style={{ color: 'var(--text-muted)' }}>
        {t.filled_at ? new Date(t.filled_at).toLocaleString() : '—'}
      </td>
    </tr>
  )
}

export function AccountDetail() {
  const { account = '' } = useParams()
  const accountMeta = ACCOUNTS.find((a) => a.account === account)
  const { portfolio, stats, performance, loading } = useAccount(account)

  if (!accountMeta) {
    return (
      <EmptyState
        message={`Unknown account: ${account}`}
        hint="Pick one from the sidebar."
      />
    )
  }

  if (loading && !portfolio) return <LoadingSpinner />

  const positions = (portfolio?.positions ?? []).filter((p) => {
    if (account === 'spreads') {
      return ['bull_put_spread', 'bear_call_spread', 'long_call_vertical'].includes(p.strategy_type)
    }
    return p.strategy_type === account
  })

  const wheelStates = portfolio?.wheel_states ?? {}
  const isWheel = account === 'wheel'

  // Last 90 days of equity curve
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - 90)
  const equityData = (performance?.equity_curve ?? []).filter(
    (p) => new Date(p.date) >= cutoff,
  )

  // We don't have a /api/trades endpoint yet — show best/worst as proxies.
  const closedTrades: Trade[] = [
    performance?.best_trade,
    performance?.worst_trade,
  ].filter((t): t is Trade => Boolean(t))

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
        <h3 className="text-sm font-semibold mb-3">
          Notable trades
          <span className="ml-2 text-xs font-normal" style={{ color: 'var(--text-muted)' }}>
            (best &amp; worst — full trade history pending /api/trades endpoint)
          </span>
        </h3>
        <div
          className="rounded overflow-hidden"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          {closedTrades.length === 0 ? (
            <EmptyState message="No closed trades yet" />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Underlying</th>
                  <th>Type</th>
                  <th>Symbol</th>
                  <th>Fill</th>
                  <th>P&amp;L</th>
                  <th>Filled</th>
                </tr>
              </thead>
              <tbody>
                {closedTrades.map((t, i) => (
                  <TradeRow key={`${t.id ?? i}-${t.symbol}`} t={t} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  )
}
