import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { api, type ContextResponse, type SourceHealthEntry } from '../api/client'
import { useAccount } from '../hooks/useAccount'
import { useAccounts } from '../hooks/useAccounts'
import { useDecisions } from '../hooks/useDecisions'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import { StatCard } from '../components/shared/StatCard'
import type {
  CircuitBreaker,
  ClaudeAgreementResponse,
  ClaudeCostsResponse,
  CycleSummaryResponse,
  Decision,
  FillQualityResponse,
  Portfolio,
  PortfolioGreeks,
  TokenUsageDaily,
  TokenUsageSummary,
  TokenUsageToday,
} from '../types'

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

function FillQualityCard() {
  const [data, setData] = useState<FillQualityResponse | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.fillQuality().then((d) => { if (!cancelled) setData(d) }).catch(() => {})
    }
    load()
    const id = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const hitRate =
    data && data.trades_analyzed > 0
      ? Math.round(
          ((data.negative_slippage_count + data.exact_fill_count) / data.trades_analyzed) * 100,
        )
      : null

  const avgSlip = data?.avg_slippage ?? 0
  const totalImpact = data?.total_slippage_dollars ?? 0

  return (
    <div>
      <h2 className="section-heading">Fill quality</h2>
      <div
        className="rounded-md p-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {!data || data.trades_analyzed === 0 ? (
          <div className="text-sm text-center py-2" style={{ color: 'var(--text-muted)' }}>
            No fills yet
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                Trades filled
              </div>
              <div
                className="text-2xl font-mono tabular"
                style={{ color: 'var(--text-primary)' }}
              >
                {data.trades_analyzed}
              </div>
            </div>
            <div>
              <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                Avg slippage
              </div>
              <div
                className="text-2xl font-mono tabular"
                style={{
                  color:
                    avgSlip < 0
                      ? 'var(--green)'
                      : avgSlip > 0
                        ? 'var(--red)'
                        : 'var(--text-muted)',
                }}
              >
                {avgSlip >= 0 ? '+' : ''}${avgSlip.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                Total impact
              </div>
              <div
                className="text-2xl font-mono tabular"
                style={{
                  color:
                    totalImpact < 0
                      ? 'var(--green)'
                      : totalImpact > 0
                        ? 'var(--red)'
                        : 'var(--text-muted)',
                }}
              >
                {totalImpact >= 0 ? '+' : ''}${totalImpact.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                Hit rate
              </div>
              <div
                className="text-2xl font-mono tabular"
                style={{ color: 'var(--text-primary)' }}
              >
                {hitRate != null ? `${hitRate}%` : '—'}
              </div>
              <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                filled at or better than limit
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── AI usage panel ───────────────────────────────────────────

function cacheHitColor(rate: number): string {
  if (rate >= 70) return 'var(--green)'
  if (rate >= 40) return 'var(--yellow, #d29922)'
  return 'var(--red)'
}

function AiUsagePanel() {
  const [today, setToday] = useState<TokenUsageToday | null>(null)
  const [summary, setSummary] = useState<TokenUsageSummary | null>(null)
  const [daily, setDaily] = useState<TokenUsageDaily[]>([])

  useEffect(() => {
    let cancelled = false

    const loadFast = () => {
      api.tokenUsageToday().then((d) => { if (!cancelled) setToday(d) }).catch(() => {})
      api.tokenUsageSummary().then((d) => { if (!cancelled) setSummary(d) }).catch(() => {})
    }
    const loadSlow = () => {
      api.tokenUsageDaily(30).then((d) => { if (!cancelled) setDaily(d.daily) }).catch(() => {})
    }

    loadFast()
    loadSlow()
    const fastId = setInterval(loadFast, 60_000)
    const slowId = setInterval(loadSlow, 5 * 60_000)
    return () => { cancelled = true; clearInterval(fastId); clearInterval(slowId) }
  }, [])

  const hasData = (today?.calls_count ?? 0) > 0 || (summary?.lifetime?.total_calls ?? 0) > 0

  return (
    <div>
      <h2 className="section-heading">AI usage</h2>
      {!hasData ? (
        <EmptyState
          icon="chart"
          message="No AI usage data yet"
          hint="Token tracking starts on the next decision cycle."
        />
      ) : (
        <div className="space-y-4">
          {/* Row 1 — today's stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="API calls today"
              value={today?.calls_count ?? 0}
            />
            <StatCard
              label="Cost today"
              value={`$${(today?.estimated_cost_usd ?? 0).toFixed(2)}`}
            />
            <StatCard
              label="Cache hit rate"
              value={`${(today?.cache_hit_rate ?? 0).toFixed(1)}%`}
              valueColor={cacheHitColor(today?.cache_hit_rate ?? 0)}
            />
            <StatCard
              label="System prompt"
              value={
                summary?.prompt_size?.system_prompt_tokens != null
                  ? `${summary.prompt_size.system_prompt_tokens.toLocaleString()} tokens`
                  : '—'
              }
              sub={
                summary?.prompt_size?.utilization_pct != null
                  ? `${summary.prompt_size.utilization_pct}% of context window`
                  : undefined
              }
            />
          </div>

          {/* Row 2 — 30-day cost chart */}
          {daily.length > 0 && (
            <div
              className="rounded-md p-4"
              style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
            >
              <div className="text-xs uppercase tracking-wider mb-3 font-semibold" style={{ color: 'var(--text-secondary)' }}>
                30-day cost (USD)
              </div>
              <ResponsiveContainer width="100%" height={140}>
                <BarChart data={[...daily].reverse()} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                    tickFormatter={(v: string) => v.slice(5)}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                    tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                    width={52}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'var(--bg-card)',
                      border: '1px solid var(--border)',
                      fontSize: 12,
                    }}
                    formatter={(value: number, name: string) => {
                      if (name === 'estimated_cost_usd') return [`$${value.toFixed(4)}`, 'Cost']
                      return [value, name]
                    }}
                    labelFormatter={(label: string) => `Date: ${label}`}
                  />
                  <Bar dataKey="estimated_cost_usd" fill="var(--accent)" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Row 3 — per-strategy breakdown */}
          {summary && (summary.by_strategy?.length ?? 0) > 0 && (
            <div
              className="rounded-md p-4"
              style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
            >
              <div className="text-xs uppercase tracking-wider mb-3 font-semibold" style={{ color: 'var(--text-secondary)' }}>
                By strategy (30 days)
              </div>
              <table style={{ width: '100%', fontSize: 12 }}>
                <thead>
                  <tr style={{ color: 'var(--text-muted)' }}>
                    <th style={{ textAlign: 'left', paddingBottom: 4 }}>Strategy</th>
                    <th style={{ textAlign: 'right', paddingBottom: 4 }}>Calls</th>
                    <th style={{ textAlign: 'right', paddingBottom: 4 }}>Total cost</th>
                    <th style={{ textAlign: 'right', paddingBottom: 4 }}>Avg/call</th>
                    <th style={{ textAlign: 'right', paddingBottom: 4 }}>Cache hit</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.by_strategy.map((s) => (
                    <tr key={s.strategy_type} style={{ color: 'var(--text-secondary)' }}>
                      <td className="font-mono" style={{ paddingTop: 4 }}>{s.strategy_type}</td>
                      <td style={{ textAlign: 'right', paddingTop: 4 }}>{s.calls}</td>
                      <td className="font-mono" style={{ textAlign: 'right', paddingTop: 4 }}>${s.total_cost_usd.toFixed(4)}</td>
                      <td className="font-mono" style={{ textAlign: 'right', paddingTop: 4 }}>${s.avg_cost_per_call.toFixed(4)}</td>
                      <td
                        className="font-mono"
                        style={{ textAlign: 'right', paddingTop: 4, color: cacheHitColor(s.cache_hit_rate) }}
                      >
                        {s.cache_hit_rate.toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Claude Costs card ────────────────────────────────────────

const CACHE_HIT_WARN_THRESHOLD = 0.85

function ClaudeCostsCard() {
  const [data, setData] = useState<ClaudeCostsResponse | null>(null)
  const [window, setWindow] = useState<'7d' | '30d' | '90d' | 'all'>('7d')

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.claudeCosts(window).then((d) => { if (!cancelled) setData(d) }).catch(() => {})
    }
    load()
    const id = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [window])

  const totalCost = data?.total_cost_usd ?? 0
  const priorCost = data?.prior_window_cost_usd ?? 0
  const delta = totalCost - priorCost
  const deltaColor = delta < 0 ? 'var(--green)' : delta > 0 ? 'var(--red)' : 'var(--text-muted)'
  const deltaSign = delta >= 0 ? '+' : ''

  const cacheHit = data?.cache_hit_rate ?? null
  const cacheHitPct = cacheHit != null ? cacheHit * 100 : null
  const cacheHitLow = cacheHit != null && cacheHit < CACHE_HIT_WARN_THRESHOLD

  const outcomes = data?.cost_by_outcome
  const outcomeTotalCost =
    outcomes
      ? outcomes.open.cost_usd + outcomes.close.cost_usd + outcomes.skip.cost_usd
      : 0

  const WINDOW_LABELS: Record<string, string> = { '7d': '7 days', '30d': '30 days', '90d': '90 days', 'all': 'All time' }

  return (
    <div>
      <div className="flex items-center justify-between mb-0">
        <h2 className="section-heading">Claude costs</h2>
        <div className="flex gap-1 mb-1">
          {(['7d', '30d', '90d', 'all'] as const).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className="text-xs px-2 py-0.5 rounded transition-colors"
              style={{
                backgroundColor: window === w ? 'var(--accent)' : 'var(--bg-secondary)',
                color: window === w ? '#fff' : 'var(--text-muted)',
                border: '1px solid var(--border)',
                cursor: 'pointer',
              }}
            >
              {w}
            </button>
          ))}
        </div>
      </div>
      <div
        className="rounded-md p-4 space-y-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {!data || data.decisions_count === 0 ? (
          <div className="text-sm text-center py-2" style={{ color: 'var(--text-muted)' }}>
            No cost data for {WINDOW_LABELS[window]}
          </div>
        ) : (
          <>
            {/* Row 1 — headline stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                  Total cost ({WINDOW_LABELS[window]})
                </div>
                <div className="text-2xl font-mono tabular" style={{ color: 'var(--text-primary)' }}>
                  ${totalCost.toFixed(2)}
                </div>
                {priorCost > 0 && (
                  <div className="text-xs mt-0.5 font-mono" style={{ color: deltaColor }}>
                    {deltaSign}${Math.abs(delta).toFixed(2)} vs prior
                  </div>
                )}
              </div>
              <div>
                <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                  Cost / filled trade
                </div>
                <div className="text-2xl font-mono tabular" style={{ color: 'var(--text-primary)' }}>
                  {data.cost_per_filled_trade_usd != null
                    ? `$${data.cost_per_filled_trade_usd.toFixed(3)}`
                    : '—'}
                </div>
                <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                  {data.filled_trades_count} trade{data.filled_trades_count !== 1 ? 's' : ''}
                </div>
              </div>
              <div>
                <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                  Cache hit rate
                </div>
                <div
                  className="text-2xl font-mono tabular"
                  style={{ color: cacheHitLow ? 'var(--red)' : cacheHitPct != null && cacheHitPct >= 85 ? 'var(--green)' : 'var(--text-primary)' }}
                >
                  {cacheHitPct != null ? `${(cacheHitPct).toFixed(1)}%` : '—'}
                  {cacheHitLow && <span className="text-sm ml-1" title="Cache hit rate below 85%">⚠</span>}
                </div>
              </div>
              <div>
                <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                  Decisions
                </div>
                <div className="text-2xl font-mono tabular" style={{ color: 'var(--text-primary)' }}>
                  {data.decisions_count}
                </div>
              </div>
            </div>

            {/* Row 2 — cost by outcome */}
            {outcomes && outcomeTotalCost > 0 && (
              <div>
                <div className="text-xs uppercase tracking-wider mb-2 font-semibold" style={{ color: 'var(--text-secondary)' }}>
                  Cost by outcome
                </div>
                <div className="space-y-1">
                  {(['open', 'close', 'skip'] as const).map((bucket) => {
                    const b = outcomes[bucket]
                    const pct = outcomeTotalCost > 0 ? (b.cost_usd / outcomeTotalCost) * 100 : 0
                    return (
                      <div key={bucket} className="flex items-center gap-2 text-xs">
                        <div className="w-12 text-right font-mono" style={{ color: 'var(--text-muted)' }}>
                          {bucket}
                        </div>
                        <div className="flex-1 rounded overflow-hidden" style={{ backgroundColor: 'var(--bg-secondary)', height: 8 }}>
                          <div
                            style={{
                              width: `${pct.toFixed(1)}%`,
                              height: '100%',
                              backgroundColor: 'var(--accent)',
                              borderRadius: 2,
                            }}
                          />
                        </div>
                        <div className="w-20 text-right font-mono" style={{ color: 'var(--text-secondary)' }}>
                          ${b.cost_usd.toFixed(4)}
                        </div>
                        <div className="w-8 text-right" style={{ color: 'var(--text-muted)' }}>
                          {b.count}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Today's Cycle card ───────────────────────────────────────

function fmtRelTime(iso: string | null): string {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(ms / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function TodaysCycleCard() {
  const [data, setData] = useState<CycleSummaryResponse | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.cycleSummary().then((d) => { if (!cancelled) setData(d) }).catch(() => {})
    }
    load()
    const id = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const ROW_STYLE: React.CSSProperties = {
    display: 'flex',
    alignItems: 'baseline',
    gap: 6,
    fontSize: 13,
    lineHeight: '1.6',
    color: 'var(--text-secondary)',
  }
  const LABEL: React.CSSProperties = { color: 'var(--text-muted)', minWidth: 160, flexShrink: 0 }
  const NUM: React.CSSProperties = { fontFamily: 'monospace', color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }

  const isEmpty = !data || data.symbols_evaluated === 0

  const headerLabel = isEmpty
    ? 'No cycle data yet'
    : `${data.cycle_type.replace(/_/g, ' ')} · ${fmtRelTime(data.cycle_started_at)}`

  const guardrailRules = data ? Object.entries(data.guardrail_rejections.by_rule) : []
  const preCheckReasons = data ? Object.entries(data.pre_check_skipped.by_reason) : []
  const orders = data ? data.orders_details : []

  return (
    <div>
      <h2 className="section-heading">Today&apos;s cycle</h2>
      <div
        className="rounded-md p-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', fontFamily: 'inherit' }}
      >
        <div className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>{headerLabel}</div>

        {isEmpty ? (
          <div className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Waiting for the first job run.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {/* Symbols evaluated */}
            <div style={ROW_STYLE}>
              <span style={LABEL}>Symbols evaluated</span>
              <span style={NUM}>{data!.symbols_evaluated}</span>
            </div>

            {/* Pre-check skipped */}
            <div style={{ ...ROW_STYLE, paddingLeft: 16 }}>
              <span style={{ ...LABEL, color: 'var(--text-muted)' }}>├─ Pre-check skipped</span>
              <span style={NUM}>{data!.pre_check_skipped.total}</span>
              {preCheckReasons.length > 0 && (
                <button
                  onClick={() => setExpanded((e) => !e)}
                  style={{ fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', padding: '0 4px' }}
                >
                  {expanded ? '▲' : '▼'}
                </button>
              )}
            </div>
            {expanded && preCheckReasons.map(([reason, count]) => (
              <div key={reason} style={{ ...ROW_STYLE, paddingLeft: 36, fontSize: 12 }}>
                <span style={{ ...LABEL, color: 'var(--text-muted)', fontSize: 12 }}>│  {reason}</span>
                <span style={{ ...NUM, fontSize: 12 }}>{count}</span>
              </div>
            ))}

            {/* Sent to Claude */}
            <div style={{ ...ROW_STYLE, paddingLeft: 16 }}>
              <span style={{ ...LABEL, color: 'var(--text-muted)' }}>├─ Sent to Claude</span>
              <span style={NUM}>{data!.sent_to_claude}</span>
            </div>

            {/* Claude outcomes */}
            {(data!.claude_outcomes.SKIP > 0 || data!.claude_outcomes.OPEN > 0 ||
              data!.claude_outcomes.CLOSE > 0 || data!.claude_outcomes.HOLD > 0) && (
              <>
                {data!.claude_outcomes.SKIP > 0 && (
                  <div style={{ ...ROW_STYLE, paddingLeft: 32 }}>
                    <span style={{ ...LABEL, color: 'var(--text-muted)', fontSize: 12 }}>│   ├─ Claude SKIP</span>
                    <span style={{ ...NUM, fontSize: 12 }}>{data!.claude_outcomes.SKIP}</span>
                  </div>
                )}
                {data!.claude_outcomes.OPEN > 0 && (
                  <div style={{ ...ROW_STYLE, paddingLeft: 32 }}>
                    <span style={{ ...LABEL, color: 'var(--text-muted)', fontSize: 12 }}>│   ├─ Claude OPEN</span>
                    <span style={{ ...NUM, fontSize: 12 }}>{data!.claude_outcomes.OPEN}</span>
                  </div>
                )}
                {data!.claude_outcomes.CLOSE > 0 && (
                  <div style={{ ...ROW_STYLE, paddingLeft: 32 }}>
                    <span style={{ ...LABEL, color: 'var(--text-muted)', fontSize: 12 }}>│   ├─ Claude CLOSE</span>
                    <span style={{ ...NUM, fontSize: 12 }}>{data!.claude_outcomes.CLOSE}</span>
                  </div>
                )}
                {data!.claude_outcomes.HOLD > 0 && (
                  <div style={{ ...ROW_STYLE, paddingLeft: 32 }}>
                    <span style={{ ...LABEL, color: 'var(--text-muted)', fontSize: 12 }}>│   └─ Claude HOLD</span>
                    <span style={{ ...NUM, fontSize: 12 }}>{data!.claude_outcomes.HOLD}</span>
                  </div>
                )}
              </>
            )}

            {/* Guardrail rejections */}
            {data!.guardrail_rejections.total > 0 && (
              <>
                <div style={{ ...ROW_STYLE, paddingLeft: 48 }}>
                  <span style={{ fontSize: 12, color: 'var(--red)', minWidth: 144 }}>│       ├─ Guardrail rej.</span>
                  <span style={{ ...NUM, fontSize: 12, color: 'var(--red)' }}>{data!.guardrail_rejections.total}</span>
                </div>
                {guardrailRules.map(([rule, count]) => (
                  <div key={rule} style={{ ...ROW_STYLE, paddingLeft: 64, fontSize: 11 }}>
                    <span style={{ color: 'var(--text-muted)', minWidth: 128, fontSize: 11 }}>│        {rule}</span>
                    <span style={{ ...NUM, fontSize: 11, color: 'var(--red)' }}>{count}</span>
                  </div>
                ))}
              </>
            )}

            {/* Orders placed */}
            {data!.orders_placed > 0 && (
              <div style={{ ...ROW_STYLE, paddingLeft: 48 }}>
                <span style={{ fontSize: 12, color: 'var(--green)', minWidth: 144 }}>│       └─ Order placed</span>
                <span style={{ ...NUM, fontSize: 12, color: 'var(--green)' }}>{data!.orders_placed}</span>
              </div>
            )}
            {orders.map((o, i) => (
              <div key={i} style={{ ...ROW_STYLE, paddingLeft: 64, fontSize: 11 }}>
                <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                  {o.symbol} {o.contract ?? ''}{o.limit_price != null ? ` @ $${Math.abs(o.limit_price).toFixed(2)}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Claude Agreement card ────────────────────────────────────

function ClaudeAgreementCard() {
  const [data, setData] = useState<ClaudeAgreementResponse | null>(null)
  const [window, setWindow] = useState<'7d' | '30d' | '90d' | 'all'>('30d')

  useEffect(() => {
    let cancelled = false
    const load = () => {
      api.claudeAgreement(window).then((d) => { if (!cancelled) setData(d) }).catch(() => {})
    }
    load()
    const id = setInterval(load, 5 * 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [window])

  const skipRate = data?.claude_skip_rate_when_precheck_says_open
  const skipRatePct = skipRate != null ? (skipRate * 100).toFixed(1) : null
  const openRate = data?.claude_open_rate_when_precheck_says_skip
  const openRatePct = openRate != null ? (openRate * 100).toFixed(1) : null
  const sampleSize = data?.precheck_open_count ?? 0

  const interpretation = skipRate == null
    ? null
    : skipRate > 0.6
      ? 'Claude is more selective than pre-check.'
      : skipRate < 0.2
        ? 'Claude is rubber-stamping pre-check verdicts.'
        : 'Claude and pre-check are roughly aligned.'

  const WINDOW_LABELS: Record<string, string> = { '7d': '7d', '30d': '30d', '90d': '90d', 'all': 'all' }

  return (
    <div>
      <div className="flex items-center justify-between mb-0">
        <h2 className="section-heading">Claude agreement</h2>
        <div className="flex gap-1 mb-1">
          {(['7d', '30d', '90d', 'all'] as const).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className="text-xs px-2 py-0.5 rounded transition-colors"
              style={{
                backgroundColor: window === w ? 'var(--accent)' : 'var(--bg-secondary)',
                color: window === w ? '#fff' : 'var(--text-muted)',
                border: '1px solid var(--border)',
                cursor: 'pointer',
              }}
            >
              {WINDOW_LABELS[w]}
            </button>
          ))}
        </div>
      </div>
      <div
        className="rounded-md p-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {!data || data.decisions_evaluated === 0 ? (
          <div className="text-sm text-center py-2" style={{ color: 'var(--text-muted)' }}>
            No entry decisions recorded for {window === 'all' ? 'all time' : `last ${window}`}
          </div>
        ) : (
          <>
            {/* Headline metric */}
            <div className="mb-3">
              <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>
                Claude SKIP rate when pre-check says OPEN ({window}
                {sampleSize > 0 ? `, n=${sampleSize}` : ''})
              </div>
              <div
                className="text-4xl font-mono tabular"
                style={{ color: skipRatePct != null ? 'var(--text-primary)' : 'var(--text-muted)' }}
              >
                {skipRatePct != null ? `${skipRatePct}%` : '—'}
              </div>
              {interpretation && (
                <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                  {interpretation}
                </div>
              )}
            </div>

            {/* Sparkline */}
            {data.daily_series.length > 1 && (
              <div className="mb-3">
                <ResponsiveContainer width="100%" height={60}>
                  <BarChart
                    data={data.daily_series}
                    margin={{ top: 2, right: 4, left: 0, bottom: 0 }}
                  >
                    <XAxis dataKey="date" hide />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'var(--bg-card)',
                        border: '1px solid var(--border)',
                        fontSize: 11,
                      }}
                      formatter={(value: number, _name: string) => [`${(value * 100).toFixed(1)}%`, 'Skip rate']}
                      labelFormatter={(label: string) => label}
                    />
                    <Bar
                      dataKey="skip_when_open_rate"
                      fill="var(--accent)"
                      radius={[2, 2, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Secondary stat */}
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
              Claude OPEN rate when pre-check says SKIP:{' '}
              <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>
                {openRatePct != null ? `${openRatePct}%` : '—'}
              </span>
              {data.precheck_skip_count > 0 && (
                <span style={{ marginLeft: 4 }}>(n={data.precheck_skip_count})</span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── PortfolioGreeksCard ─────────────────────────────────────────────────────

const fmtSigned = (n: number | null | undefined, decimals = 2) => {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toLocaleString(undefined, { maximumFractionDigits: decimals, minimumFractionDigits: decimals })}`
}

const DTE_BUCKET_LABELS: Record<string, string> = {
  '0_7': '0–7 DTE',
  '8_21': '8–21 DTE',
  '22_45': '22–45 DTE',
  '46_plus': '46+ DTE',
}

function PortfolioGreeksCardContent({ data }: { data: PortfolioGreeks }) {
  const [dteOpen, setDteOpen] = useState(false)
  const fr = data.freshness
  const ageSeconds = fr.newest_contract_age_seconds
  const ageLabel = ageSeconds < 120 ? `${ageSeconds}s` : `${Math.round(ageSeconds / 60)}m`
  const stale = fr.max_skew_seconds > 300 || fr.contracts_from_fallback_source > 0

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-3">
        {(
          [
            { label: 'Net Delta', value: fmtSigned(data.net_delta, 2) },
            { label: 'Net Theta', value: `${fmtSigned(data.net_theta, 2)}/day` },
            { label: 'Net Vega', value: fmtSigned(data.net_vega, 2) },
            { label: 'Defined Risk', value: data.total_defined_risk_usd ? `$${data.total_defined_risk_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—' },
          ] as { label: string; value: string }[]
        ).map(({ label, value }) => (
          <div key={label}>
            <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>{label}</div>
            <div className="text-xl font-mono tabular" style={{ color: 'var(--text-primary)' }}>{value}</div>
          </div>
        ))}
      </div>

      {/* By DTE collapsible */}
      <button
        onClick={() => setDteOpen((o) => !o)}
        className="text-xs flex items-center gap-1 mb-2"
        style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--text-muted)' }}
      >
        <span style={{ transform: dteOpen ? 'rotate(90deg)' : 'rotate(0deg)', display: 'inline-block', transition: 'transform 0.15s ease', lineHeight: 1 }}>▶</span>
        By DTE
      </button>
      {dteOpen && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3 text-xs">
          {(['0_7', '8_21', '22_45', '46_plus'] as const).map((bkt) => {
            const b = data.by_dte_bucket[bkt]
            return (
              <div
                key={bkt}
                className="rounded p-2"
                style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
              >
                <div className="font-semibold mb-1" style={{ color: 'var(--text-secondary)' }}>
                  {DTE_BUCKET_LABELS[bkt]}
                </div>
                <div style={{ color: 'var(--text-muted)' }}>θ {fmtSigned(b.theta, 2)}/day</div>
                <div style={{ color: 'var(--text-muted)' }}>ν {fmtSigned(b.vega, 2)}</div>
                <div style={{ color: 'var(--text-muted)' }}>{b.position_count} pos</div>
              </div>
            )
          })}
        </div>
      )}

      {/* Freshness */}
      <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
        <span>Computed {ageLabel} ago · skew {fr.max_skew_seconds}s</span>
        {stale && (
          <span
            className="px-1.5 py-0.5 rounded text-[10px] font-semibold"
            style={{
              backgroundColor: 'color-mix(in srgb, var(--yellow) 20%, var(--bg-card))',
              color: 'var(--yellow)',
              border: '1px solid var(--yellow)',
            }}
          >
            {fr.contracts_from_fallback_source > 0
              ? `${fr.contracts_from_fallback_source} leg${fr.contracts_from_fallback_source > 1 ? 's' : ''} missing Greeks`
              : 'stale data'}
          </span>
        )}
      </div>
    </div>
  )
}

export function PortfolioGreeksCard({ account, badge }: { account?: string; badge?: ReactNode }) {
  const [data, setData] = useState<PortfolioGreeks | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.portfolioGreeks(account)
      .then((d) => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch(() => { if (!cancelled) setLoading(false) })
    const id = setInterval(() => {
      api.portfolioGreeks(account)
        .then((d) => { if (!cancelled) setData(d) })
        .catch(() => {})
    }, 5 * 60_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [account])

  return (
    <div
      className="rounded p-4"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      <div className="flex items-center gap-2 mb-3">
        <div className="text-sm font-semibold" style={{ color: 'var(--text-secondary)' }}>
          Portfolio Greeks{account ? ` — ${account}` : ''}
        </div>
        {badge}
      </div>
      {loading ? (
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading…</div>
      ) : !data ? (
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>No data yet</div>
      ) : (
        <PortfolioGreeksCardContent data={data} />
      )}
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
  const [pendingCount, setPendingCount] = useState<number>(0)

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
      api.pendingCount().then((r) => { if (!cancelled) setPendingCount(r.count) }).catch(() => {})
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
        <div className="flex items-center gap-3">
          <h2 className="section-heading">Accounts</h2>
          {pendingCount > 0 && (
            <span
              className="text-xs px-2 py-0.5 rounded font-medium"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--yellow, #eab308) 20%, transparent)',
                color: 'var(--yellow, #eab308)',
                border: '1px solid var(--yellow, #eab308)',
              }}
            >
              {pendingCount} order{pendingCount !== 1 ? 's' : ''} pending fill
            </span>
          )}
        </div>
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

      <FillQualityCard />

      <ClaudeCostsCard />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <TodaysCycleCard />
        <ClaudeAgreementCard />
      </div>

      <PortfolioGreeksCard />

      <DataHealthPanel />

      <AiUsagePanel />

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
