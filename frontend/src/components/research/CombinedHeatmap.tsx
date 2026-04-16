import { useEffect, useState } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface LiqScore {
  symbol: string
  strategy_type: string
  tier: string
  confidence: string
  composite_score: number | null
}

interface WrStat {
  symbol: string
  strategy_type: string
  win_rate: number
  trade_count: number
  confidence: string
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

// ── Multiplier helpers (mirrors strategy Python code) ─────────────────────────

function liqMultiplier(tier: string, confidence: string): number {
  if (confidence === 'none') return 1.0
  if (confidence === 'low') return 1.0
  // high confidence
  const m: Record<string, number> = { A: 1.2, B: 1.0, C: 0.8, D: 0.0 }
  return m[tier] ?? 1.0
}

function wrMultiplier(winRate: number, confidence: string): number {
  if (confidence === 'none') return 1.0
  if (confidence === 'low') return 1.0
  // high confidence
  if (winRate < 0.30) return 0.0
  if (winRate >= 0.70) return 1.3
  if (winRate >= 0.60) return 1.15
  if (winRate >= 0.50) return 1.0
  if (winRate >= 0.40) return 0.85
  return 0.7
}

function combinedColor(combined: number): { bg: string; fg: string } {
  if (combined === 0.0) return { bg: 'rgba(127,29,29,0.60)', fg: '#fca5a5' }
  if (combined >= 1.35)  return { bg: 'rgba(34,197,94,0.35)',  fg: '#22c55e' }
  if (combined >= 1.15)  return { bg: 'rgba(34,197,94,0.18)',  fg: '#86efac' }
  if (combined >= 1.0)   return { bg: 'rgba(234,179,8,0.20)',  fg: '#eab308' }
  if (combined >= 0.85)  return { bg: 'rgba(249,115,22,0.25)', fg: '#fb923c' }
  if (combined >= 0.70)  return { bg: 'rgba(239,68,68,0.25)',  fg: '#ef4444' }
  return { bg: 'rgba(100,116,139,0.18)', fg: 'var(--text-muted)' }
}

// ── Data fetching & building lookup ──────────────────────────────────────────

type LiqLookup = Record<string, Record<string, LiqScore>>
type WrLookup  = Record<string, Record<string, WrStat>>

function buildLiqLookup(strategies: Record<string, LiqScore[]>): LiqLookup {
  const map: LiqLookup = {}
  for (const [, rows] of Object.entries(strategies)) {
    for (const r of rows) {
      if (!map[r.symbol]) map[r.symbol] = {}
      map[r.symbol][r.strategy_type] = r
    }
  }
  return map
}

function buildWrLookup(strategies: Record<string, WrStat[]>): WrLookup {
  const map: WrLookup = {}
  for (const [, rows] of Object.entries(strategies)) {
    for (const r of rows) {
      if (!map[r.symbol]) map[r.symbol] = {}
      map[r.symbol][r.strategy_type] = r
    }
  }
  return map
}

function allSymbols(liqL: LiqLookup, wrL: WrLookup): string[] {
  const set = new Set([...Object.keys(liqL), ...Object.keys(wrL)])
  return Array.from(set).sort()
}

// ── Component ─────────────────────────────────────────────────────────────────

export function CombinedHeatmap({ onSymbolClick }: Props) {
  const [liqData, setLiqData] = useState<Record<string, LiqScore[]> | null>(null)
  const [wrData, setWrData]   = useState<Record<string, WrStat[]> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [tooltip, setTooltip] = useState<{ x: number; y: number; content: string } | null>(null)

  useEffect(() => {
    Promise.all([
      fetch('/api/research/liquidity/scores', { credentials: 'same-origin' }).then((r) =>
        r.ok ? r.json() : Promise.reject(`liquidity: ${r.statusText}`),
      ),
      fetch('/api/research/winrate/symbol-stats', { credentials: 'same-origin' }).then((r) =>
        r.ok ? r.json() : Promise.reject(`winrate: ${r.statusText}`),
      ),
    ])
      .then(([liq, wr]) => {
        setLiqData(liq.strategies)
        setWrData(wr.strategies)
        setLoading(false)
      })
      .catch((e) => { setError(String(e)); setLoading(false) })
  }, [])

  if (loading) return <div style={{ color: 'var(--text-muted)' }} className="py-8 text-center">Loading combined data…</div>
  if (error) return <div style={{ color: 'var(--red)' }} className="py-4">Error: {error}</div>
  if (!liqData || !wrData) return null

  const liqL = buildLiqLookup(liqData)
  const wrL  = buildWrLookup(wrData)
  const symbols = allSymbols(liqL, wrL)

  if (symbols.length === 0) {
    return (
      <div className="py-12 text-center" style={{ color: 'var(--text-muted)' }}>
        No data yet. Run the weekly scan and backtest sweep to populate.
      </div>
    )
  }

  return (
    <div className="relative overflow-x-auto">
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
                const liq = liqL[sym]?.[col.key]
                const wr  = wrL[sym]?.[col.key]

                if (!liq && !wr) {
                  return (
                    <td key={col.key} className="py-2 px-3 text-center">
                      <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>
                    </td>
                  )
                }

                const lm = liq ? liqMultiplier(liq.tier, liq.confidence) : 1.0
                const wm = wr  ? wrMultiplier(wr.win_rate, wr.confidence) : 1.0
                const combined = lm * wm
                const { bg, fg } = combinedColor(combined)

                const isReject = combined === 0.0
                const label = isReject ? '✕' : combined.toFixed(2) + '×'
                const dominatedBy =
                  lm === 0.0 ? 'liq floor' :
                  wm === 0.0 ? 'wr floor' :
                  lm > wm    ? 'liq-led' : wm > lm ? 'wr-led' : 'equal'

                return (
                  <td
                    key={col.key}
                    className="py-2 px-3 text-center"
                    onMouseEnter={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect()
                      setTooltip({
                        x: rect.left,
                        y: rect.top,
                        content: [
                          `${sym} · ${col.label}`,
                          `Combined: ${combined.toFixed(3)}×`,
                          `Liq (Tier ${liq?.tier ?? '?'}, ${liq?.confidence ?? 'none'}): ${lm.toFixed(2)}×`,
                          `WR (${wr ? (wr.win_rate * 100).toFixed(1) + '%' : '—'}, ${wr?.confidence ?? 'none'}): ${wm.toFixed(2)}×`,
                          `Driver: ${dominatedBy}`,
                        ].join('\n'),
                      })
                    }}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    <span
                      className="inline-block rounded px-2 py-0.5 font-mono font-semibold text-xs"
                      style={{ backgroundColor: bg, color: fg }}
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
          { val: 1.56, label: '≥1.35×' },
          { val: 1.20, label: '≥1.15×' },
          { val: 1.0,  label: '≥1.00×' },
          { val: 0.85, label: '≥0.85×' },
          { val: 0.70, label: '≥0.70×' },
          { val: 0.0,  label: '0.0× reject' },
        ].map(({ val, label }) => {
          const { bg, fg } = combinedColor(val)
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
