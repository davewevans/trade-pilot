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
import type { CircuitBreaker, EquityHistory, Position, Trade } from '../types'

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
  const upl = p.unrealized_pnl
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
      <td className="font-mono tabular text-xs">
        {p.delta != null ? p.delta.toFixed(2) : '—'}
      </td>
      <td className="font-mono tabular text-xs">
        {p.theta != null ? p.theta.toFixed(2) : '—'}
      </td>
      <td className="font-mono tabular text-xs">
        {p.dte != null ? p.dte : '—'}
      </td>
      <td
        className="font-mono tabular text-xs"
        style={{
          color: upl == null ? 'var(--text-muted)'
            : upl >= 0 ? 'var(--green)' : 'var(--red)',
        }}
      >
        {upl == null ? '—' : fmtSigned(Number(upl))}
      </td>
    </tr>
  )
}

export function AccountDetail() {
  const { account = '' } = useParams()
  const accountMeta = ACCOUNTS.find((a) => a.account === account)
  const { portfolio, stats, loading } = useAccount(account)

  const [trades, setTrades] = useState<Trade[] | null>(null)
  const [tradesLoading, setTradesLoading] = useState(true)
  const [cb, setCb] = useState<CircuitBreaker | null>(null)
  const [equityHistory, setEquityHistory] = useState<EquityHistory | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.circuitBreakers().then((c) => { if (!cancelled) setCb(c) }).catch(() => {
        if (!cancelled) setCb(null)
      })
    }
    load()
    const id = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.equityHistory().then((h) => { if (!cancelled) setEquityHistory(h) }).catch(() => {
        if (!cancelled) setEquityHistory(null)
      })
    }
    load()
    const id = setInterval(load, 5 * 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

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

  const equityPoints = equityHistory?.points ?? []
  const baseValue = equityHistory?.base_value ?? 0
  const lastEquity = equityPoints.length
    ? equityPoints[equityPoints.length - 1].equity
    : null
  const equityLineColor =
    lastEquity == null || baseValue === 0 ? 'var(--accent)'
      : lastEquity >= baseValue ? 'var(--green)' : 'var(--red)'

  const todayPnlVal = portfolio?.account?.today_pnl
  const todayPnlPct = portfolio?.account?.today_pnl_pct
  const bpUsedPct = portfolio?.account?.buying_power_used_pct
  const todayColor =
    todayPnlVal == null || todayPnlVal === 0 ? 'var(--text-muted)'
      : todayPnlVal > 0 ? 'var(--green)' : 'var(--red)'
  const todayDisplay = todayPnlVal == null
    ? '—'
    : `${fmtSigned(todayPnlVal)} (${todayPnlPct != null ? `${todayPnlPct >= 0 ? '+' : ''}${todayPnlPct.toFixed(2)}%` : '—'})`

  const cbStatus = cb?.status
  const cbHalted = cb?.halted
  const cbBadgeText = cbStatus === 'RED' && cbHalted ? 'HALTED' : cbStatus

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <div>
          <Link to="/" className="text-xs" style={{ color: 'var(--text-muted)' }}>
            ← Dashboard
          </Link>
          <div className="flex items-center gap-2 mt-1">
            <h2 className="text-2xl font-semibold">{accountMeta.label}</h2>
            {cbStatus && <Badge variant="circuit">{cbBadgeText}</Badge>}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
        <StatCard
          label="Portfolio value"
          value={fmtMoney(portfolio?.account?.total_equity)}
          size="lg"
        />
        <StatCard
          label="Buying power"
          value={fmtMoney(portfolio?.account?.buying_power)}
          sub={bpUsedPct ? `${bpUsedPct.toFixed(0)}% deployed` : undefined}
          size="lg"
        />
        <StatCard
          label="Today"
          value={<span style={{ color: todayColor }}>{todayDisplay}</span>}
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
                  <th>Theta</th>
                  <th>DTE</th>
                  <th>Unrealized</th>
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
          {equityPoints.length === 0 ? (
            <EmptyState message="No equity data yet" />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={equityPoints} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="var(--text-muted)" fontSize={11} />
                <YAxis stroke="var(--text-muted)" fontSize={11} domain={['auto', 'auto']} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'var(--bg-secondary)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                  }}
                  formatter={(value: number, name: string) => {
                    if (name === 'equity') return [fmtMoney(value), 'Equity']
                    if (name === 'pnl') return [fmtSigned(value), 'Daily P&L']
                    return [value, name]
                  }}
                />
                {baseValue > 0 && (
                  <ReferenceLine y={baseValue} stroke="var(--text-muted)" strokeDasharray="3 3" />
                )}
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke={equityLineColor}
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="pnl"
                  stroke="transparent"
                  dot={false}
                  legendType="none"
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
