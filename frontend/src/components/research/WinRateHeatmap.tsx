import { useEffect, useState } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SymbolStat {
  symbol: string
  strategy_type: string
  trade_count: number
  win_rate: number
  avg_pnl_per_trade: number
  total_pnl: number
  max_drawdown: number
  sharpe_ratio: number | null
  confidence: string
  date_range_start: string | null
  date_range_end: string | null
  last_updated: string | null
}

interface WinRateData {
  generated_at: string
  strategies: Record<string, SymbolStat[]>
}

interface Props {
  onSymbolClick: (symbol: string) => void
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STRATEGY_COLS = [
  { key: 'wheel_csp', label: 'Wheel' },
  { key: 'bull_put_spread', label: 'BPS' },
  { key: 'bear_call_spread', label: 'BCS' },
  { key: 'iron_condor', label: 'IC' },
  { key: 'long_call_vertical', label: 'LCV' },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function wrTier(wr: number): { label: string; bg: string; fg: string } {
  if (wr >= 0.70) return { label: 'strong',  bg: 'rgba(34,197,94,0.35)',  fg: '#22c55e' }
  if (wr >= 0.60) return { label: 'good',    bg: 'rgba(34,197,94,0.18)',  fg: '#86efac' }
  if (wr >= 0.50) return { label: 'neutral', bg: 'rgba(234,179,8,0.20)',  fg: '#eab308' }
  if (wr >= 0.40) return { label: 'weak',    bg: 'rgba(249,115,22,0.25)', fg: '#fb923c' }
  if (wr >= 0.30) return { label: 'poor',    bg: 'rgba(239,68,68,0.25)',  fg: '#ef4444' }
  return              { label: 'reject',  bg: 'rgba(127,29,29,0.50)',  fg: '#fca5a5' }
}

function buildLookup(data: WinRateData): Record<string, Record<string, SymbolStat>> {
  const map: Record<string, Record<string, SymbolStat>> = {}
  for (const [strat, rows] of Object.entries(data.strategies)) {
    for (const row of rows) {
      if (!map[row.symbol]) map[row.symbol] = {}
      map[row.symbol][strat] = row
    }
  }
  return map
}

function allSymbolsSorted(data: WinRateData): string[] {
  const scoreMap: Record<string, number> = {}
  for (const rows of Object.values(data.strategies)) {
    for (const r of rows) {
      if (!scoreMap[r.symbol]) scoreMap[r.symbol] = 0
      scoreMap[r.symbol] += r.win_rate
    }
  }
  return Object.keys(scoreMap).sort((a, b) => scoreMap[b] - scoreMap[a])
}

function fmt$(n: number | null | undefined): string {
  if (n == null) return '—'
  return (n >= 0 ? '+' : '') + n.toFixed(0)
}

// ── Component ─────────────────────────────────────────────────────────────────

export function WinRateHeatmap({ onSymbolClick }: Props) {
  const [data, setData] = useState<WinRateData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showLowConf, setShowLowConf] = useState(true)
  const [tooltip, setTooltip] = useState<{
    x: number; y: number; content: string
  } | null>(null)

  useEffect(() => {
    fetch('/api/research/winrate/symbol-stats', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then((d) => { setData(d); setLoading(false) })
      .catch((e) => { setError(String(e)); setLoading(false) })
  }, [])

  if (loading) return <div style={{ color: 'var(--text-muted)' }} className="py-8 text-center">Loading win-rate data…</div>
  if (error) return <div style={{ color: 'var(--red)' }} className="py-4">Error: {error}</div>
  if (!data) return null

  const lookup = buildLookup(data)
  const symbols = allSymbolsSorted(data)

  if (symbols.length === 0) {
    return (
      <div className="py-12 text-center" style={{ color: 'var(--text-muted)' }}>
        No win-rate data yet. The weekly backtest sweep populates this table.
      </div>
    )
  }

  return (
    <div className="relative overflow-x-auto">
      {/* Confidence toggle */}
      <div className="mb-3 flex items-center gap-3">
        <label className="flex items-center gap-2 cursor-pointer select-none text-sm" style={{ color: 'var(--text-secondary)' }}>
          <input
            type="checkbox"
            checked={showLowConf}
            onChange={(e) => setShowLowConf(e.target.checked)}
            className="rounded"
          />
          Show low-confidence cells
        </label>
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          (≥ 30 trades = high, ≥ 10 = low)
        </span>
      </div>

      {tooltip && (
        <div
          className="fixed z-50 text-xs rounded px-2 py-1 pointer-events-none"
          style={{
            left: tooltip.x + 12,
            top: tooltip.y - 8,
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            maxWidth: 240,
            whiteSpace: 'pre-line',
          }}
        >
          {tooltip.content}
        </div>
      )}

      <table className="w-full text-sm">
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            <th
              className="text-left py-2 px-3 sticky left-0"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--text-muted)',
                minWidth: 80,
                fontSize: 11,
              }}
            >
              Symbol
            </th>
            {STRATEGY_COLS.map((col) => (
              <th
                key={col.key}
                className="py-2 px-3 text-center"
                style={{ color: 'var(--text-muted)', fontSize: 11, minWidth: 80 }}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {symbols.map((sym) => (
            <tr
              key={sym}
              className="cursor-pointer transition-colors"
              style={{ borderBottom: '1px solid var(--border)' }}
              onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.04)')}
              onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = '')}
              onClick={() => onSymbolClick(sym)}
            >
              <td
                className="py-2 px-3 font-mono font-semibold sticky left-0"
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  color: 'var(--text-primary)',
                  fontSize: 12,
                }}
              >
                {sym}
              </td>
              {STRATEGY_COLS.map((col) => {
                const cell = lookup[sym]?.[col.key]
                if (!cell) {
                  return (
                    <td key={col.key} className="py-2 px-3 text-center">
                      <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>
                    </td>
                  )
                }

                // Low-confidence hidden when toggle is off
                const isLow = cell.confidence === 'low'
                const isNone = cell.confidence === 'none'
                if (isNone || (isLow && !showLowConf)) {
                  return (
                    <td key={col.key} className="py-2 px-3 text-center">
                      <span
                        className="inline-block rounded px-2 py-0.5 font-mono text-xs"
                        style={{
                          backgroundColor: 'rgba(100,116,139,0.18)',
                          color: 'var(--text-muted)',
                        }}
                      >
                        —
                      </span>
                    </td>
                  )
                }

                const { bg, fg } = wrTier(cell.win_rate)
                const opacity = isLow ? 0.55 : 1.0
                const label = `${(cell.win_rate * 100).toFixed(0)}%`

                return (
                  <td
                    key={col.key}
                    className="py-2 px-3 text-center"
                    onMouseEnter={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect()
                      const dr =
                        cell.date_range_start && cell.date_range_end
                          ? `${cell.date_range_start.slice(0, 10)} → ${cell.date_range_end.slice(0, 10)}`
                          : '—'
                      setTooltip({
                        x: rect.left,
                        y: rect.top,
                        content: [
                          `${sym} · ${col.label}`,
                          `Win rate: ${(cell.win_rate * 100).toFixed(1)}%`,
                          `Trades: ${cell.trade_count} (${cell.confidence})`,
                          `Avg P&L: $${fmt$(cell.avg_pnl_per_trade)}`,
                          `Sharpe: ${cell.sharpe_ratio?.toFixed(2) ?? '—'}`,
                          `Range: ${dr}`,
                        ].join('\n'),
                      })
                    }}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    <span
                      className="inline-block rounded px-2 py-0.5 font-mono font-semibold text-xs"
                      style={{ backgroundColor: bg, color: fg, opacity }}
                    >
                      {label}
                    </span>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
        {[
          { wr: 0.75, label: '≥70% strong' },
          { wr: 0.65, label: '≥60% good' },
          { wr: 0.55, label: '≥50% neutral' },
          { wr: 0.45, label: '≥40% weak' },
          { wr: 0.35, label: '≥30% poor' },
          { wr: 0.20, label: '<30% reject' },
        ].map(({ wr, label }) => {
          const { bg, fg } = wrTier(wr)
          return (
            <span key={label} className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: bg }} />
              <span style={{ color: fg }}>{label}</span>
            </span>
          )
        })}
      </div>
    </div>
  )
}
