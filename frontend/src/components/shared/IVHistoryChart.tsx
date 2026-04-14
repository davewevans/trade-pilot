/**
 * IVHistoryChart — reusable area chart showing 1-year IV rank history.
 *
 * Features:
 *  - Area fill shaded green above IVR 30 and red below (gradient trick)
 *  - Reference lines at 30 (entry threshold) and 50 (high-IV marker)
 *  - Custom dots for bot trade entries, colour-coded by whether the entry
 *    was above or below the 30 threshold
 */

import { useEffect, useState } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../../api/client'
import type { IVHistoryPoint, IVTradeMarker } from '../../api/client'
import { LoadingSpinner } from './LoadingSpinner'

// ── Helpers ───────────────────────────────────────────────────────────────────

const DAYS_OPTIONS = [
  { label: '3 mo', value: 90 },
  { label: '6 mo', value: 180 },
  { label: '1 yr', value: 365 },
  { label: '2 yr', value: 730 },
]

function strategyLabel(s: string | null): string {
  if (!s) return 'Trade'
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

// Build a Set of dates that have trade markers so the custom dot renderer
// can look them up by O(1).
function buildMarkerMap(markers: IVTradeMarker[]): Map<string, IVTradeMarker[]> {
  const m = new Map<string, IVTradeMarker[]>()
  for (const mk of markers) {
    const existing = m.get(mk.date) ?? []
    existing.push(mk)
    m.set(mk.date, existing)
  }
  return m
}

// ── Custom dot for trade entry markers ───────────────────────────────────────

interface EntryDotProps {
  cx?: number
  cy?: number
  payload?: IVHistoryPoint & { _markers?: IVTradeMarker[] }
}

function EntryDot({ cx, cy, payload }: EntryDotProps) {
  if (!cx || !cy || !payload?._markers?.length) return null
  const ivr = payload.iv_rank_1y ?? 0
  const goodEntry = ivr >= 30
  const fill = goodEntry ? '#22c55e' : '#ef4444'
  return (
    <g>
      <circle cx={cx} cy={cy} r={5} fill={fill} stroke="var(--bg-card)" strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={9} fill={fill} fillOpacity={0.18} />
    </g>
  )
}

// ── Custom tooltip ────────────────────────────────────────────────────────────

interface TooltipPayloadEntry {
  payload: IVHistoryPoint & { _markers?: IVTradeMarker[] }
}

function IVTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayloadEntry[] }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  const markers = d._markers ?? []
  return (
    <div
      className="text-xs rounded p-2 space-y-1"
      style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border)',
        color: 'var(--text-primary)',
        minWidth: 140,
      }}
    >
      <div style={{ color: 'var(--text-muted)' }}>{d.date}</div>
      {d.iv_rank_1y != null && (
        <div>
          IVR (1y):{' '}
          <span style={{ color: d.iv_rank_1y >= 30 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
            {d.iv_rank_1y.toFixed(1)}
          </span>
        </div>
      )}
      {d.iv_rank_1m != null && (
        <div style={{ color: 'var(--text-muted)' }}>
          IVR (1m): {d.iv_rank_1m.toFixed(1)}
        </div>
      )}
      {d.iv != null && (
        <div style={{ color: 'var(--text-muted)' }}>
          ATM IV: {d.iv.toFixed(1)}%
        </div>
      )}
      {markers.map((mk, i) => (
        <div
          key={i}
          className="mt-1 pt-1"
          style={{ borderTop: '1px solid var(--border)', color: 'var(--accent)' }}
        >
          Bot entry: {strategyLabel(mk.strategy_type)}
        </div>
      ))}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  symbol: string
  /** If true the symbol picker and days selector are hidden (used in AccountDetail). */
  compact?: boolean
}

export function IVHistoryChart({ symbol, compact = false }: Props) {
  const [days, setDays] = useState(365)
  const [ivHistory, setIvHistory] = useState<IVHistoryPoint[]>([])
  const [markers, setMarkers] = useState<IVTradeMarker[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!symbol) return
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .ivHistory(symbol, days)
      .then((r) => {
        if (cancelled) return
        setIvHistory(r.iv_history)
        setMarkers(r.trade_markers)
      })
      .catch(() => {
        if (!cancelled) setError('IV history unavailable')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [symbol, days])

  // Merge trade markers into chart data as a `_markers` field so the custom
  // dot renderer can look them up without an extra data series.
  const markerMap = buildMarkerMap(markers)
  const chartData = ivHistory.map((pt) => ({
    ...pt,
    _markers: markerMap.get(pt.date),
  }))

  // Compute a summary label for the header
  const recentPoints = chartData.slice(-30)
  const belowThresholdDays = recentPoints.filter(
    (p) => p.iv_rank_1y != null && p.iv_rank_1y < 30,
  ).length
  const summaryNote =
    recentPoints.length > 0 && belowThresholdDays >= 15
      ? `${symbol} has been below IVR 30 for ${belowThresholdDays} of the last 30 days — bot skips are expected.`
      : null

  return (
    <div className="space-y-3">
      {!compact && (
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
            {symbol} — IV Rank History
          </div>
          <div className="flex gap-1">
            {DAYS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setDays(opt.value)}
                className="px-2 py-0.5 rounded text-xs transition-colors"
                style={{
                  backgroundColor:
                    days === opt.value
                      ? 'color-mix(in srgb, var(--accent) 20%, var(--bg-card))'
                      : 'var(--bg-card)',
                  color: days === opt.value ? 'var(--accent)' : 'var(--text-muted)',
                  border: `1px solid ${days === opt.value ? 'var(--accent)' : 'var(--border)'}`,
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {compact && (
        <div className="flex items-center justify-between">
          <div className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
            IV Rank history
          </div>
          <div className="flex gap-1">
            {DAYS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setDays(opt.value)}
                className="px-2 py-0.5 rounded text-xs transition-colors"
                style={{
                  backgroundColor:
                    days === opt.value
                      ? 'color-mix(in srgb, var(--accent) 20%, var(--bg-card))'
                      : 'transparent',
                  color: days === opt.value ? 'var(--accent)' : 'var(--text-muted)',
                  border: `1px solid ${days === opt.value ? 'var(--accent)' : 'transparent'}`,
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {summaryNote && (
        <div
          className="text-xs px-3 py-2 rounded"
          style={{
            backgroundColor: 'color-mix(in srgb, #ef4444 10%, var(--bg-card))',
            border: '1px solid color-mix(in srgb, #ef4444 30%, transparent)',
            color: '#ef4444',
          }}
        >
          {summaryNote}
        </div>
      )}

      <div style={{ height: compact ? 200 : 260 }}>
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <LoadingSpinner />
          </div>
        ) : error ? (
          <div
            className="flex items-center justify-center h-full text-sm"
            style={{ color: 'var(--text-muted)' }}
          >
            {error}
          </div>
        ) : chartData.length === 0 ? (
          <div
            className="flex items-center justify-center h-full text-sm"
            style={{ color: 'var(--text-muted)' }}
          >
            No IV history data for {symbol}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <defs>
                {/*
                 * Gradient: domain is 0–100. IVR 30 sits at 70% from the top
                 * (30 / 100 = 30% from bottom = 70% from top in SVG coords).
                 * Green above the line, red below.
                 */}
                <linearGradient id={`ivGrad-${symbol}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22c55e" stopOpacity={0.30} />
                  <stop offset="70%" stopColor="#22c55e" stopOpacity={0.06} />
                  <stop offset="70%" stopColor="#ef4444" stopOpacity={0.06} />
                  <stop offset="100%" stopColor="#ef4444" stopOpacity={0.30} />
                </linearGradient>
              </defs>

              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />

              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                tickFormatter={(v: string) => v.slice(5)}   // show MM-DD only
                interval="preserveStartEnd"
              />
              <YAxis
                domain={[0, 100]}
                tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                width={28}
                tickCount={6}
              />

              <Tooltip content={<IVTooltip />} />

              {/* ── Reference lines ────────────────────────────────────── */}
              <ReferenceLine
                y={30}
                stroke="#22c55e"
                strokeDasharray="5 3"
                strokeWidth={1.5}
                label={{
                  value: 'Entry (30)',
                  position: 'insideTopLeft',
                  fontSize: 9,
                  fill: '#22c55e',
                }}
              />
              <ReferenceLine
                y={50}
                stroke="var(--text-muted)"
                strokeDasharray="5 3"
                strokeWidth={1}
                label={{
                  value: 'High IV (50)',
                  position: 'insideTopLeft',
                  fontSize: 9,
                  fill: 'var(--text-muted)',
                }}
              />

              {/* ── IV rank area ───────────────────────────────────────── */}
              <Area
                type="monotone"
                dataKey="iv_rank_1y"
                stroke="var(--accent)"
                strokeWidth={1.5}
                fill={`url(#ivGrad-${symbol})`}
                dot={(props) => {
                  // Only render dots for trade-entry dates
                  const markers = (props.payload as IVHistoryPoint & { _markers?: IVTradeMarker[] })._markers
                  if (!markers?.length) return <g key={props.key} />
                  return <EntryDot key={props.key} cx={props.cx} cy={props.cy} payload={props.payload} />
                }}
                activeDot={{ r: 3, fill: 'var(--accent)' }}
                connectNulls
              />

              {/* Hidden series so Scatter can share the same Y axis */}
              <Scatter data={[]} dataKey="iv_rank_1y" />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ── Legend ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-4 text-xs" style={{ color: 'var(--text-muted)' }}>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-full" style={{ backgroundColor: '#22c55e' }} />
          Above 30 — selling conditions
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-full" style={{ backgroundColor: '#ef4444' }} />
          Below 30 — bot skips
        </span>
        {markers.length > 0 && (
          <span className="flex items-center gap-1">
            <span
              className="inline-block w-3 h-3 rounded-full border"
              style={{ backgroundColor: 'var(--accent)', borderColor: 'var(--bg-card)', borderWidth: 1.5 }}
            />
            Trade entries ({markers.length})
          </span>
        )}
      </div>
    </div>
  )
}
