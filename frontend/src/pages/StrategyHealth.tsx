import { useEffect, useState } from 'react'
import { api, FillRealismResponse } from '../api/client'
import { useFeatureFlags } from '../hooks/useFeatureFlags'

// ── Types ─────────────────────────────────────────────────────────────────────

interface FunnelRow {
  strategy_type: string
  iso_week: string
  week_start_date: string
  decisions_total: number
  skip_pre_check: number
  skip_claude: number
  skip_guardrail: number
  skip_circuit_breaker: number
  skip_liquidity_floor: number
  skip_winrate_floor: number
  skip_no_candidate: number
  skip_data_missing: number
  skip_halted: number
  skip_unclassified: number
  hold: number
  actions_proposed: number
  trades_submitted: number
  trades_filled: number
  trades_pending: number
  trades_closed_profit: number
  trades_closed_loss: number
  trades_closed_breakeven: number
  trades_closed_unknown: number
  top_claude_skip_reason: string | null
  top_guardrail_reason: string | null
}

interface HealthResponse {
  weeks: FunnelRow[]
  generated_at: string
  paper_mode: boolean
}

// ── Grouped column definitions ────────────────────────────────────────────────
// The default funnel view aggregates the 9 raw skip buckets into 4 groups for
// readability. The "Show detail" toggle expands to all raw buckets.

interface GroupedCol {
  label: string
  tooltip: string
  compute: (r: FunnelRow) => number
}

const GROUPED_SKIP_COLS: GroupedCol[] = [
  {
    label: 'Skip: Decision',
    tooltip:
      'skip_claude + skip_guardrail — Claude proposed something, one decision layer rejected it',
    compute: (r) => r.skip_claude + r.skip_guardrail,
  },
  {
    label: 'Skip: Pre-entry',
    tooltip:
      'skip_pre_check + skip_liquidity_floor + skip_winrate_floor + skip_no_candidate + skip_data_missing — never reached Claude',
    compute: (r) =>
      r.skip_pre_check +
      r.skip_liquidity_floor +
      r.skip_winrate_floor +
      r.skip_no_candidate +
      r.skip_data_missing,
  },
  {
    label: 'Skip: Halted',
    tooltip: 'skip_circuit_breaker + skip_halted — system-level block',
    compute: (r) => r.skip_circuit_breaker + r.skip_halted,
  },
  {
    label: 'Skip: Unclassified',
    tooltip:
      'skip_gate is NULL or unrecognised — should be near zero. Non-zero values are a data integrity signal.',
    compute: (r) => r.skip_unclassified,
  },
]

interface DetailCol {
  label: string
  key: keyof FunnelRow
  tooltip: string
}

const DETAIL_SKIP_COLS: DetailCol[] = [
  { label: 'Pre-check', key: 'skip_pre_check', tooltip: 'skip_gate = pre_check' },
  { label: 'Claude', key: 'skip_claude', tooltip: 'skip_gate = claude_skip' },
  { label: 'Guardrail', key: 'skip_guardrail', tooltip: 'skip_gate = guardrail' },
  { label: 'Circuit Br.', key: 'skip_circuit_breaker', tooltip: 'skip_gate = circuit_breaker' },
  { label: 'Liq. Floor', key: 'skip_liquidity_floor', tooltip: 'skip_gate = liquidity_floor' },
  { label: 'WR Floor', key: 'skip_winrate_floor', tooltip: 'skip_gate = winrate_floor' },
  { label: 'No Cand.', key: 'skip_no_candidate', tooltip: 'skip_gate = no_candidate' },
  { label: 'Data Miss.', key: 'skip_data_missing', tooltip: 'skip_gate = data_missing' },
  { label: 'Halted', key: 'skip_halted', tooltip: 'skip_gate = halted' },
  {
    label: 'Unclass.',
    key: 'skip_unclassified',
    tooltip: 'skip_gate is NULL or not recognised',
  },
]

// ── Cell shading ─────────────────────────────────────────────────────────────

function skipRateBg(count: number, total: number): string {
  if (total === 0 || count === 0) return 'transparent'
  const rate = count / total
  if (rate > 0.5) return 'color-mix(in srgb, #f59e0b 18%, transparent)'
  return 'transparent'
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Th({
  children,
  title,
  style,
}: {
  children: React.ReactNode
  title?: string
  style?: React.CSSProperties
}) {
  return (
    <th
      title={title}
      className="px-2 py-2 text-left text-[11px] font-semibold whitespace-nowrap"
      style={{
        color: 'var(--text-muted)',
        borderBottom: '1px solid var(--border)',
        ...style,
      }}
    >
      {children}
    </th>
  )
}

function Td({
  children,
  bg,
  title,
  style,
}: {
  children: React.ReactNode
  bg?: string
  title?: string
  style?: React.CSSProperties
}) {
  return (
    <td
      title={title}
      className="px-2 py-1.5 text-[12px] tabular-nums"
      style={{
        color: 'var(--text-primary)',
        backgroundColor: bg ?? 'transparent',
        ...style,
      }}
    >
      {children}
    </td>
  )
}

function NumCell({ value, total }: { value: number; total: number }) {
  return (
    <Td bg={skipRateBg(value, total)}>
      {value === 0 ? <span style={{ color: 'var(--text-muted)' }}>—</span> : value}
    </Td>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function StrategyHealth() {
  const [data, setData] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Controls
  const [selectedStrategy, setSelectedStrategy] = useState<string>('__all__')
  const [view, setView] = useState<'funnel' | 'timeseries'>('funnel')
  const [showDetail, setShowDetail] = useState(false)
  const [weekIndex, setWeekIndex] = useState(0) // 0 = most recent week

  const featureFlags = useFeatureFlags()
  const [fillRealism, setFillRealism] = useState<FillRealismResponse | null>(null)
  const [fillRealismLoading, setFillRealismLoading] = useState(false)

  useEffect(() => {
    if (!featureFlags.shadow_execution_enabled) return
    setFillRealismLoading(true)
    api
      .fillRealism()
      .then((d) => {
        setFillRealism(d)
        setFillRealismLoading(false)
      })
      .catch(() => setFillRealismLoading(false))
  }, [featureFlags.shadow_execution_enabled])

  const WEEKS_BACK = 12

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetch(`/api/strategy-health?weeks=${WEEKS_BACK}`, { credentials: 'same-origin' })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d: HealthResponse) => {
        setData(d)
        setLoading(false)
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'Failed to load strategy health data')
        setLoading(false)
      })
  }, [])

  if (loading) {
    return (
      <div className="p-6" style={{ color: 'var(--text-secondary)' }}>
        Loading strategy health data…
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="p-6" style={{ color: 'var(--red)' }}>
        {error ?? 'No data'}
      </div>
    )
  }

  const rows = data.weeks

  // Derive strategy list and week list from data
  const allStrategies = Array.from(new Set(rows.map((r) => r.strategy_type))).sort()
  const allWeeks = Array.from(new Set(rows.map((r) => r.iso_week))).sort().reverse()

  // Clamp weekIndex
  const safeWeekIndex = Math.max(0, Math.min(weekIndex, allWeeks.length - 1))
  const currentWeek = allWeeks[safeWeekIndex]

  // Filter helpers
  const rowsForWeek = (iw: string) =>
    rows.filter(
      (r) =>
        r.iso_week === iw &&
        (selectedStrategy === '__all__' || r.strategy_type === selectedStrategy),
    )

  const rowsForStrategy = (st: string) =>
    rows.filter((r) => r.strategy_type === st).sort((a, b) => b.iso_week.localeCompare(a.iso_week))

  // Funnel view: one row per strategy for the selected week
  const funnelRows = rowsForWeek(currentWeek)

  // Time series view: weeks as rows, one strategy
  const timeSeriesStrategy =
    selectedStrategy === '__all__'
      ? allStrategies[0] ?? null
      : selectedStrategy
  const timeSeriesRows = timeSeriesStrategy ? rowsForStrategy(timeSeriesStrategy) : []

  return (
    <div className="space-y-4">
      {/* ── Fill Realism summary ──────────────────────────────────────────── */}
      {featureFlags.shadow_execution_enabled && (
        <FillRealismSection data={fillRealism} loading={fillRealismLoading} />
      )}

      {/* ── Warning banner (always visible, not dismissible) ─────────────── */}
      <div
        className="rounded-lg p-4 text-sm leading-relaxed"
        style={{
          backgroundColor: 'color-mix(in srgb, #f59e0b 12%, var(--bg-card))',
          border: '1px solid color-mix(in srgb, #f59e0b 40%, transparent)',
          color: 'var(--text-primary)',
        }}
        role="status"
        aria-live="polite"
      >
        <span className="font-semibold" style={{ color: '#d97706' }}>
          Paper-trading observability.
        </span>{' '}
        These counts show where each strategy dies in the decision funnel and which skip reasons
        dominate — useful for prompt iteration. Win/loss counts reflect Claude's decision quality,
        not realistic profitability. Paper fills assume mid-price execution with no slippage or fees
        and CANNOT be used to decide whether a strategy should keep running live.
      </div>

      {/* ── Controls ─────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Strategy selector */}
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
            Strategy
          </label>
          <select
            value={selectedStrategy}
            onChange={(e) => {
              setSelectedStrategy(e.target.value)
              setWeekIndex(0)
            }}
            className="rounded px-2 py-1 text-sm"
            style={{
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
          >
            <option value="__all__">All strategies</option>
            {allStrategies.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        {/* View toggle */}
        <div
          className="flex rounded overflow-hidden"
          style={{ border: '1px solid var(--border)' }}
        >
          {(['funnel', 'timeseries'] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className="px-3 py-1 text-sm"
              style={{
                backgroundColor:
                  view === v
                    ? 'color-mix(in srgb, var(--accent) 20%, var(--bg-card))'
                    : 'var(--bg-card)',
                color: view === v ? 'var(--text-primary)' : 'var(--text-muted)',
                fontWeight: view === v ? 600 : 400,
                border: 'none',
                cursor: 'pointer',
              }}
            >
              {v === 'funnel' ? 'Funnel view' : 'Time series view'}
            </button>
          ))}
        </div>

        {/* Detail toggle */}
        <button
          onClick={() => setShowDetail((p) => !p)}
          className="px-3 py-1 rounded text-sm"
          style={{
            backgroundColor: showDetail
              ? 'color-mix(in srgb, var(--accent) 20%, var(--bg-card))'
              : 'var(--bg-card)',
            border: '1px solid var(--border)',
            color: showDetail ? 'var(--text-primary)' : 'var(--text-muted)',
            cursor: 'pointer',
          }}
        >
          {showDetail ? 'Hide detail' : 'Show detail'}
        </button>
      </div>

      {/* ── Week navigator (funnel view only) ────────────────────────────── */}
      {view === 'funnel' && allWeeks.length > 0 && (
        <div className="flex items-center gap-2">
          <button
            onClick={() => setWeekIndex((i) => Math.min(i + 1, allWeeks.length - 1))}
            disabled={safeWeekIndex >= allWeeks.length - 1}
            className="p-1 rounded"
            style={{
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border)',
              color:
                safeWeekIndex >= allWeeks.length - 1
                  ? 'var(--text-muted)'
                  : 'var(--text-primary)',
              cursor: safeWeekIndex >= allWeeks.length - 1 ? 'default' : 'pointer',
            }}
            title="Previous week"
          >
            &#8592;
          </button>
          <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
            Week {currentWeek}
            {funnelRows[0] && (
              <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                {' '}
                (starts {funnelRows[0].week_start_date})
              </span>
            )}
          </span>
          <button
            onClick={() => setWeekIndex((i) => Math.max(i - 1, 0))}
            disabled={safeWeekIndex <= 0}
            className="p-1 rounded"
            style={{
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border)',
              color: safeWeekIndex <= 0 ? 'var(--text-muted)' : 'var(--text-primary)',
              cursor: safeWeekIndex <= 0 ? 'default' : 'pointer',
            }}
            title="Next week"
          >
            &#8594;
          </button>
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {safeWeekIndex + 1} of {allWeeks.length} weeks
          </span>
        </div>
      )}

      {/* ── Tables ───────────────────────────────────────────────────────── */}
      {view === 'funnel' ? (
        <FunnelTable rows={funnelRows} showDetail={showDetail} />
      ) : (
        <>
          {selectedStrategy === '__all__' && (
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              Select a single strategy to view the time series.
            </p>
          )}
          {timeSeriesStrategy && (
            <TimeSeriesTable
              strategy={timeSeriesStrategy}
              rows={timeSeriesRows}
              showDetail={showDetail}
            />
          )}
        </>
      )}

      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        Generated {data.generated_at} UTC · paper mode
      </p>
    </div>
  )
}

// ── Fill Realism section ──────────────────────────────────────────────────────

function realismColor(pct: number, gatePct: number): string {
  if (pct >= gatePct) return 'var(--green)'
  if (pct >= gatePct - 20) return '#f59e0b'
  return 'var(--red)'
}

function RealismCell({
  pct,
  sampleSize,
  gateSample,
  gatePct,
}: {
  pct: number | null
  sampleSize: number
  gateSample: number
  gatePct: number
}) {
  if (sampleSize < gateSample) {
    return (
      <Td>
        <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          n={sampleSize}, insufficient data
        </span>
      </Td>
    )
  }
  if (pct === null) {
    return (
      <Td>
        <span style={{ color: 'var(--text-muted)' }}>—</span>
      </Td>
    )
  }
  return (
    <Td>
      <span style={{ color: realismColor(pct, gatePct), fontWeight: 600 }}>
        {pct.toFixed(1)}%
      </span>
    </Td>
  )
}

function FillRealismSection({
  data,
  loading,
}: {
  data: FillRealismResponse | null
  loading: boolean
}) {
  return (
    <div
      className="rounded-lg p-4"
      style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-card)' }}
    >
      <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
        Fill Realism (90-day)
      </h2>
      <p className="text-xs mb-3 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
        Measures whether paper-submitted limit prices would have filled against the real NBBO
        (ORATS primary, Alpaca fallback). A blended signal because paper limits use Alpaca's
        15-min-delayed quotes but are measured against real-time NBBO at submission and follow-up
        intervals.
      </p>

      {loading && (
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Loading fill realism data…
        </p>
      )}

      {!loading && !data && (
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          No fill realism data yet.
        </p>
      )}

      {!loading && data && data.strategies.length === 0 && (
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          No shadow executions recorded yet.
        </p>
      )}

      {!loading && data && data.strategies.length > 0 && (
        <div className="overflow-x-auto rounded-lg" style={{ border: '1px solid var(--border)' }}>
          <table className="w-full border-collapse text-left">
            <thead>
              <tr style={{ backgroundColor: 'var(--bg-secondary)' }}>
                <Th>Strategy</Th>
                <Th title="Total snapshots in the window">Sample</Th>
                <Th title="% fillable at T+2min (always_fillable + sometimes_fillable); excludes data_unavailable from denominator">
                  T+2m realism
                </Th>
                <Th title="% fillable at EOD; excludes data_unavailable from denominator">
                  EOD realism
                </Th>
                <Th title={`Gate: sample ≥ ${data.gate_sample} AND realism ≥ ${data.gate_pct}%`}>
                  Gate
                </Th>
                <Th title="Snapshots where NBBO was unavailable or crossed — excluded from realism %">
                  Data unavail.
                </Th>
              </tr>
            </thead>
            <tbody>
              {data.strategies.map((row) => (
                <tr key={row.strategy_type} style={{ borderBottom: '1px solid var(--border)' }}>
                  <Td style={{ fontWeight: 600 }}>{row.strategy_type}</Td>
                  <Td>{row.sample_size}</Td>
                  <RealismCell
                    pct={row.t2m_realism_pct}
                    sampleSize={row.sample_size}
                    gateSample={data.gate_sample}
                    gatePct={data.gate_pct}
                  />
                  <RealismCell
                    pct={row.eod_realism_pct}
                    sampleSize={row.eod_sample_size}
                    gateSample={data.gate_sample}
                    gatePct={data.gate_pct}
                  />
                  <Td>
                    {row.gate_met ? (
                      <span style={{ color: 'var(--green)', fontWeight: 600 }}>Pass</span>
                    ) : (
                      <span style={{ color: 'var(--red)' }}>Fail</span>
                    )}
                  </Td>
                  <Td>
                    {row.data_unavailable_count > 0 ? (
                      <span style={{ color: 'var(--text-muted)' }}>
                        {row.data_unavailable_count}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && data && (
        <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
          Gate threshold: sample ≥ {data.gate_sample} · realism ≥ {data.gate_pct}% · green ≥{' '}
          {data.gate_pct}% · yellow ≥ {data.gate_pct - 20}% · red &lt; {data.gate_pct - 20}%
        </p>
      )}
    </div>
  )
}

// ── Funnel view table ─────────────────────────────────────────────────────────

function FunnelTable({
  rows,
  showDetail,
}: {
  rows: FunnelRow[]
  showDetail: boolean
}) {
  if (rows.length === 0) {
    return (
      <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
        No data for this week.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg" style={{ border: '1px solid var(--border)' }}>
      <table className="w-full border-collapse text-left">
        <thead>
          <tr style={{ backgroundColor: 'var(--bg-secondary)' }}>
            <Th>Strategy</Th>
            <Th title="Total decisions this week">Total</Th>
            {showDetail
              ? DETAIL_SKIP_COLS.map((c) => (
                  <Th key={c.key} title={c.tooltip}>
                    {c.label}
                  </Th>
                ))
              : GROUPED_SKIP_COLS.map((c) => (
                  <Th key={c.label} title={c.tooltip}>
                    {c.label}
                  </Th>
                ))}
            <Th title="Top skip_reason_code among claude_skip rows">Top Claude reason</Th>
            <Th title="Top reasoning text prefix among guardrail rows">Top guardrail reason</Th>
            <Th title="action = HOLD">Hold</Th>
            <Th title="action not in SKIP or HOLD">Proposed</Th>
            <Th title="Trades submitted">Submitted</Th>
            <Th title="fill_price IS NOT NULL">Filled</Th>
            <Th title="fill_price IS NULL AND outcome IS NULL">Pending</Th>
            <Th title="outcome = profit">Closed ✓</Th>
            <Th title="outcome = loss">Closed ✗</Th>
            <Th title="outcome = breakeven">BE</Th>
            <Th title="outcome = unknown — integrity signal">?</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <FunnelRow key={r.strategy_type} row={r} showDetail={showDetail} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FunnelRow({ row: r, showDetail }: { row: FunnelRow; showDetail: boolean }) {
  const total = r.decisions_total
  return (
    <tr style={{ borderBottom: '1px solid var(--border)' }}>
      <Td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{r.strategy_type}</Td>
      <Td>{total === 0 ? <span style={{ color: 'var(--text-muted)' }}>—</span> : total}</Td>

      {showDetail
        ? DETAIL_SKIP_COLS.map((c) => (
            <NumCell key={c.key} value={r[c.key] as number} total={total} />
          ))
        : GROUPED_SKIP_COLS.map((c) => (
            <NumCell key={c.label} value={c.compute(r)} total={total} />
          ))}

      <Td title={r.top_claude_skip_reason ?? undefined}>
        {r.top_claude_skip_reason ? (
          <code className="text-[11px]">{r.top_claude_skip_reason}</code>
        ) : (
          <span style={{ color: 'var(--text-muted)' }}>—</span>
        )}
      </Td>
      <Td title={r.top_guardrail_reason ?? undefined}>
        {r.top_guardrail_reason ? (
          <span className="text-[11px]">{r.top_guardrail_reason}</span>
        ) : (
          <span style={{ color: 'var(--text-muted)' }}>—</span>
        )}
      </Td>

      <NumCell value={r.hold} total={total} />
      <NumCell value={r.actions_proposed} total={total} />
      <Td>{r.trades_submitted || <span style={{ color: 'var(--text-muted)' }}>—</span>}</Td>
      <Td>{r.trades_filled || <span style={{ color: 'var(--text-muted)' }}>—</span>}</Td>
      <Td>{r.trades_pending || <span style={{ color: 'var(--text-muted)' }}>—</span>}</Td>
      <Td style={{ color: r.trades_closed_profit > 0 ? 'var(--green)' : undefined }}>
        {r.trades_closed_profit || <span style={{ color: 'var(--text-muted)' }}>—</span>}
      </Td>
      <Td style={{ color: r.trades_closed_loss > 0 ? 'var(--red)' : undefined }}>
        {r.trades_closed_loss || <span style={{ color: 'var(--text-muted)' }}>—</span>}
      </Td>
      <Td>{r.trades_closed_breakeven || <span style={{ color: 'var(--text-muted)' }}>—</span>}</Td>
      <Td style={{ color: r.trades_closed_unknown > 0 ? '#f59e0b' : undefined }}>
        {r.trades_closed_unknown || <span style={{ color: 'var(--text-muted)' }}>—</span>}
      </Td>
    </tr>
  )
}

// ── Time series view table ────────────────────────────────────────────────────

function TimeSeriesTable({
  strategy,
  rows,
  showDetail,
}: {
  strategy: string
  rows: FunnelRow[]
  showDetail: boolean
}) {
  if (rows.length === 0) {
    return (
      <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
        No data for {strategy}.
      </p>
    )
  }

  return (
    <div>
      <h2 className="text-sm font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>
        {strategy} — weekly funnel
      </h2>
      <div className="overflow-x-auto rounded-lg" style={{ border: '1px solid var(--border)' }}>
        <table className="w-full border-collapse text-left">
          <thead>
            <tr style={{ backgroundColor: 'var(--bg-secondary)' }}>
              <Th>Week</Th>
              <Th>Starts</Th>
              <Th title="Total decisions">Total</Th>
              {showDetail
                ? DETAIL_SKIP_COLS.map((c) => (
                    <Th key={c.key} title={c.tooltip}>
                      {c.label}
                    </Th>
                  ))
                : GROUPED_SKIP_COLS.map((c) => (
                    <Th key={c.label} title={c.tooltip}>
                      {c.label}
                    </Th>
                  ))}
              <Th title="Top claude skip reason">Top Claude reason</Th>
              <Th title="Top guardrail reason">Top guardrail reason</Th>
              <Th>Hold</Th>
              <Th>Proposed</Th>
              <Th>Submitted</Th>
              <Th>Filled</Th>
              <Th>Pending</Th>
              <Th title="outcome = profit">Closed ✓</Th>
              <Th title="outcome = loss">Closed ✗</Th>
              <Th title="outcome = breakeven">BE</Th>
              <Th title="outcome = unknown">?</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const total = r.decisions_total
              return (
                <tr key={r.iso_week} style={{ borderBottom: '1px solid var(--border)' }}>
                  <Td style={{ fontWeight: 600 }}>{r.iso_week}</Td>
                  <Td style={{ color: 'var(--text-muted)' }}>{r.week_start_date}</Td>
                  <Td>{total || <span style={{ color: 'var(--text-muted)' }}>—</span>}</Td>

                  {showDetail
                    ? DETAIL_SKIP_COLS.map((c) => (
                        <NumCell key={c.key} value={r[c.key] as number} total={total} />
                      ))
                    : GROUPED_SKIP_COLS.map((c) => (
                        <NumCell key={c.label} value={c.compute(r)} total={total} />
                      ))}

                  <Td title={r.top_claude_skip_reason ?? undefined}>
                    {r.top_claude_skip_reason ? (
                      <code className="text-[11px]">{r.top_claude_skip_reason}</code>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </Td>
                  <Td title={r.top_guardrail_reason ?? undefined}>
                    {r.top_guardrail_reason ? (
                      <span className="text-[11px]">{r.top_guardrail_reason}</span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </Td>

                  <NumCell value={r.hold} total={total} />
                  <NumCell value={r.actions_proposed} total={total} />
                  <Td>{r.trades_submitted || <span style={{ color: 'var(--text-muted)' }}>—</span>}</Td>
                  <Td>{r.trades_filled || <span style={{ color: 'var(--text-muted)' }}>—</span>}</Td>
                  <Td>{r.trades_pending || <span style={{ color: 'var(--text-muted)' }}>—</span>}</Td>
                  <Td style={{ color: r.trades_closed_profit > 0 ? 'var(--green)' : undefined }}>
                    {r.trades_closed_profit || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                  </Td>
                  <Td style={{ color: r.trades_closed_loss > 0 ? 'var(--red)' : undefined }}>
                    {r.trades_closed_loss || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                  </Td>
                  <Td>
                    {r.trades_closed_breakeven || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                  </Td>
                  <Td style={{ color: r.trades_closed_unknown > 0 ? '#f59e0b' : undefined }}>
                    {r.trades_closed_unknown || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                  </Td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
