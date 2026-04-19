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
import { api } from '../api/client'
import { SCREENING_FILTERS } from '../data/screeningFilters'
import { useAccount } from '../hooks/useAccount'
import { useAccounts } from '../hooks/useAccounts'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import { IVHistoryChart } from '../components/shared/IVHistoryChart'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import { StatCard } from '../components/shared/StatCard'
import { PortfolioGreeksCard } from './Dashboard'
import type { CircuitBreaker, EquityHistory, NtaEvent, NtaEventsResponse, Position, Trade } from '../types'

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
  if (!entries.length) return (
    <EmptyState
      icon="wheel"
      message="No wheel state yet"
      hint="Watchlist symbols will appear here once the bot starts cycling."
    />
  )
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
  const { accounts, availableStrategies, refetch: refetchAccounts } = useAccounts()
  const accountMeta = accounts.find((a) => a.account_id === account)
  const { portfolio, stats, loading } = useAccount(account)

  const [trades, setTrades] = useState<Trade[] | null>(null)
  const [tradesLoading, setTradesLoading] = useState(true)
  const [cb, setCb] = useState<CircuitBreaker | null>(null)
  const [equityHistory, setEquityHistory] = useState<EquityHistory | null>(null)
  const [ivSymbol, setIvSymbol] = useState<string>('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [selectedStrategy, setSelectedStrategy] = useState('')
  const [ntaEvents, setNtaEvents] = useState<NtaEventsResponse | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)

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

  // Fetch NTA events once for the wheel account (assignments/expirations)
  useEffect(() => {
    if (account !== 'wheel') return
    let cancelled = false
    api.ntaEvents(30)
      .then((r) => { if (!cancelled) setNtaEvents(r) })
      .catch(() => { if (!cancelled) setNtaEvents({ events: [], total: 0, error: 'Unable to load NTA events' }) })
    return () => { cancelled = true }
  }, [account])

  const handleToggleActive = async () => {
    if (!accountMeta) return
    setActionError(null)
    setActionLoading(true)
    try {
      if (accountMeta.status === 'active') {
        await api.deactivateAccount(account)
      } else {
        await api.activateAccount(account)
      }
      refetchAccounts()
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setActionLoading(false)
    }
  }

  const handleLinkStrategy = async () => {
    if (!selectedStrategy) return
    setActionError(null)
    setActionLoading(true)
    try {
      await api.linkStrategy(account, selectedStrategy)
      refetchAccounts()
      setSelectedStrategy('')
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setActionLoading(false)
    }
  }

  // Show loading spinner while accounts are still fetching and account not yet found
  if (accounts.length === 0 && loading) return <LoadingSpinner />

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

  // Collect unique underlying symbols from positions + recent trades for the IV picker.
  const accountSymbols = Array.from(new Set([
    ...positions.map((p) => p.underlying ?? p.symbol?.slice(0, 4) ?? '').filter(Boolean),
    ...(trades ?? []).map((t) => t.underlying ?? '').filter(Boolean),
  ])).slice(0, 12)

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
      <div className="flex items-start justify-between">
        <div>
          <Link to="/" className="text-xs" style={{ color: 'var(--text-muted)' }}>
            ← Dashboard
          </Link>
          <div className="flex items-center gap-2 mt-1">
            <h2 className="text-2xl font-semibold">{accountMeta.label}</h2>
            {cbStatus && <Badge variant="circuit">{cbBadgeText}</Badge>}
            <span
              className="text-xs px-2 py-0.5 rounded"
              style={{
                backgroundColor: accountMeta.status === 'active'
                  ? 'color-mix(in srgb, var(--green) 15%, var(--bg-card))'
                  : 'color-mix(in srgb, var(--text-muted) 15%, var(--bg-card))',
                color: accountMeta.status === 'active' ? 'var(--green)' : 'var(--text-muted)',
                border: `1px solid ${accountMeta.status === 'active' ? 'var(--green)' : 'var(--border)'}`,
              }}
            >
              {accountMeta.status}
            </span>
          </div>
          {accountMeta.strategy_display_name && (
            <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
              Strategy: <span style={{ color: 'var(--text-secondary)' }}>{accountMeta.strategy_display_name}</span>
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-2">
          {/* Activate / Deactivate toggle */}
          <button
            onClick={handleToggleActive}
            disabled={actionLoading || (!accountMeta.strategy && accountMeta.status === 'inactive')}
            className="text-xs px-3 py-1.5 rounded transition-colors"
            style={{
              backgroundColor: accountMeta.status === 'active'
                ? 'color-mix(in srgb, var(--red) 15%, var(--bg-card))'
                : 'color-mix(in srgb, var(--green) 15%, var(--bg-card))',
              color: accountMeta.status === 'active' ? 'var(--red)' : 'var(--green)',
              border: `1px solid ${accountMeta.status === 'active' ? 'var(--red)' : 'var(--green)'}`,
              opacity: actionLoading ? 0.6 : 1,
              cursor: actionLoading || (!accountMeta.strategy && accountMeta.status === 'inactive') ? 'not-allowed' : 'pointer',
            }}
            title={!accountMeta.strategy && accountMeta.status === 'inactive' ? 'Link a strategy first' : undefined}
          >
            {actionLoading ? '…' : accountMeta.status === 'active' ? 'Deactivate' : 'Activate'}
          </button>
          {/* Link strategy — only shown if no strategy linked yet */}
          {!accountMeta.strategy && (
            <div className="flex items-center gap-2">
              <select
                value={selectedStrategy}
                onChange={(e) => setSelectedStrategy(e.target.value)}
                className="text-xs px-2 py-1.5 rounded"
                style={{
                  backgroundColor: 'var(--bg-card)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
              >
                <option value="">Link a strategy…</option>
                {availableStrategies.map((s) => (
                  <option key={s.name} value={s.name}>{s.display_name}</option>
                ))}
              </select>
              <button
                onClick={handleLinkStrategy}
                disabled={!selectedStrategy || actionLoading}
                className="text-xs px-3 py-1.5 rounded transition-colors"
                style={{
                  backgroundColor: 'color-mix(in srgb, var(--accent) 15%, var(--bg-card))',
                  color: 'var(--accent)',
                  border: '1px solid var(--accent)',
                  opacity: !selectedStrategy || actionLoading ? 0.4 : 1,
                  cursor: !selectedStrategy || actionLoading ? 'not-allowed' : 'pointer',
                }}
              >
                Link
              </button>
            </div>
          )}
          {actionError && (
            <div className="text-xs" style={{ color: 'var(--red)' }}>{actionError}</div>
          )}
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
          accent={
            stats?.win_rate != null
              ? stats.win_rate > 0 ? 'var(--green)' : 'var(--red)'
              : undefined
          }
          valueColor={
            stats?.win_rate != null && stats.win_rate > 0
              ? 'var(--green)'
              : undefined
          }
        />
      </div>

      {isWheel && (
        <section>
          <h3 className="section-heading">Wheel state</h3>
          <div
            className="rounded p-4"
            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
          >
            <WheelPipeline states={wheelStates} />
          </div>
        </section>
      )}

      <section>
        <h3 className="section-heading">Open positions</h3>
        <div
          className="rounded overflow-hidden"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          {positions.length === 0 ? (
            <EmptyState icon="inbox" message="No open positions" hint="New positions will show up here as the bot opens them." />
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

      <PortfolioGreeksCard account={account} />

      <section>
        <h3 className="section-heading">Equity curve (last 90 days)</h3>
        <div
          className="rounded p-3"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', height: 280 }}
        >
          {equityPoints.length === 0 ? (
            <EmptyState icon="chart" message="No equity data yet" hint="Daily snapshots accrue once the portfolio refresh job runs." />
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

      {accountSymbols.length > 0 && (
        <section>
          <div className="flex items-center gap-3 mb-2">
            <h3 className="section-heading mb-0">IV rank history</h3>
            <div className="flex flex-wrap gap-1">
              {accountSymbols.map((sym) => (
                <button
                  key={sym}
                  onClick={() => setIvSymbol(sym === ivSymbol ? '' : sym)}
                  className="px-2 py-0.5 rounded text-xs font-mono transition-colors"
                  style={{
                    backgroundColor:
                      ivSymbol === sym
                        ? 'color-mix(in srgb, var(--accent) 20%, var(--bg-card))'
                        : 'var(--bg-card)',
                    color: ivSymbol === sym ? 'var(--accent)' : 'var(--text-muted)',
                    border: `1px solid ${ivSymbol === sym ? 'var(--accent)' : 'var(--border)'}`,
                  }}
                >
                  {sym}
                </button>
              ))}
            </div>
          </div>
          {ivSymbol ? (
            <div
              className="rounded p-4"
              style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
            >
              <IVHistoryChart symbol={ivSymbol} compact />
            </div>
          ) : (
            <div
              className="rounded p-4 text-sm text-center"
              style={{
                backgroundColor: 'var(--bg-card)',
                border: '1px solid var(--border)',
                color: 'var(--text-muted)',
              }}
            >
              Select a symbol above to view its IV rank history.
            </div>
          )}
        </section>
      )}

      {accountMeta.strategy && (
        <section>
          <button
            onClick={() => setFiltersOpen((o) => !o)}
            className="flex items-center gap-2 w-full text-left"
            style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}
          >
            <h3 className="section-heading mb-0">Screening Filters</h3>
            <span
              style={{
                color: 'var(--text-muted)',
                fontSize: '0.75rem',
                transform: filtersOpen ? 'rotate(180deg)' : 'rotate(0deg)',
                transition: 'transform 0.15s ease',
                display: 'inline-block',
                lineHeight: 1,
              }}
            >
              ▾
            </span>
          </button>
          {filtersOpen && (
            <div className="space-y-3 mt-3">
              {SCREENING_FILTERS[accountMeta.strategy] ? (
                <>
                  {SCREENING_FILTERS[accountMeta.strategy].map((group) => (
                    <div
                      key={group.strategy}
                      className="rounded p-4"
                      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
                    >
                      <div
                        className="text-xs font-semibold uppercase tracking-wider mb-3"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        {group.strategy}
                      </div>
                      <div
                        style={{
                          display: 'grid',
                          gridTemplateColumns: 'repeat(2, 1fr)',
                          gap: '6px 24px',
                        }}
                      >
                        {group.filters.map((f, i) => (
                          <div key={i} className="flex items-baseline justify-between gap-2">
                            <span className="text-xs" style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                              {f.label}
                            </span>
                            <span
                              className="font-mono text-xs"
                              style={{ color: 'var(--text-primary)', textAlign: 'right' }}
                            >
                              {f.value}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                  <p className="text-xs" style={{ color: 'var(--text-muted)', marginTop: '4px' }}>
                    These filters are defined in the bot's prompt configuration and guardrails.
                  </p>
                </>
              ) : (
                <div
                  className="rounded p-4"
                  style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
                >
                  <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
                    Screening filters for <code>{accountMeta.strategy}</code> haven't been documented yet.
                    Add an entry keyed by <code>{accountMeta.strategy}</code> to{' '}
                    <code>frontend/src/data/screeningFilters.ts</code>.
                  </p>
                </div>
              )}
            </div>
          )}
        </section>
      )}

      <section>
        <h3 className="section-heading">Closed trades</h3>
        <div
          className="rounded overflow-hidden"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          {tradesLoading && trades === null ? (
            <LoadingSpinner />
          ) : !trades || trades.length === 0 ? (
            <EmptyState icon="list" message="No closed trades yet" hint="Filled trades will land here after the next reconcile." />
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

      {isWheel && (
        <section>
          <h3 className="section-heading">Assignment &amp; Expiry Events</h3>
          <div
            className="rounded overflow-hidden"
            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
          >
            {ntaEvents === null ? (
              <LoadingSpinner />
            ) : ntaEvents.error ? (
              <EmptyState icon="list" message="Unable to load NTA events" hint={ntaEvents.error} />
            ) : ntaEvents.events.length === 0 ? (
              <EmptyState icon="list" message="No assignment or expiry events" hint="Assignments, expirations, and exercises from the last 30 days will appear here." />
            ) : (
              <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {ntaEvents.events.map((e: NtaEvent, i: number) => {
                  const typeColors: Record<string, string> = {
                    assignment: 'var(--yellow)',
                    expiry: 'var(--green)',
                    exercise: 'var(--accent)',
                  }
                  const typeLabels: Record<string, string> = {
                    assignment: 'Assignment',
                    expiry: 'Expiry',
                    exercise: 'Exercise',
                  }
                  const color = typeColors[e.type] ?? 'var(--text-muted)'
                  return (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '12px',
                        padding: '8px 12px',
                        borderRadius: '6px',
                        backgroundColor: 'var(--bg-surface)',
                        border: `1px solid ${color}44`,
                      }}
                    >
                      <span style={{ color, fontWeight: 600, fontSize: '0.8rem', minWidth: '80px' }}>
                        {typeLabels[e.type] ?? e.raw_type}
                      </span>
                      <span className="font-mono" style={{ flex: 1, fontSize: '0.85rem' }}>{e.symbol}</span>
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                        {e.qty} contract{e.qty !== 1 ? 's' : ''}
                        {e.price != null ? ` @ $${e.price.toFixed(2)}` : ''}
                      </span>
                      <span className="font-mono text-xs" style={{ color: 'var(--text-muted)' }}>
                        {new Date(e.date).toLocaleDateString()}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </section>
      )}
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
      <td className="font-mono tabular">
        {t.fill_price != null ? `$${t.fill_price.toFixed(2)}` : '—'}
        {t.limit_price != null && t.fill_price != null && (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginLeft: '4px' }}>
            (asked ${t.limit_price.toFixed(2)})
          </span>
        )}
      </td>
      <td
        className="font-mono tabular"
        style={{ color: pl >= 0 ? 'var(--green)' : 'var(--red)' }}
      >
        {fmtSigned(pl)}
      </td>
    </tr>
  )
}
