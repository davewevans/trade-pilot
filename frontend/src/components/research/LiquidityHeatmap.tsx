import { useEffect, useState } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface LiquidityScore {
  symbol: string
  strategy_type: string
  composite_score: number | null
  tier: string
  confidence: string
  scored_at: string | null
}

interface LiquidityData {
  generated_at: string
  strategies: Record<string, LiquidityScore[]>
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

function tierColor(tier: string, confidence: string): string {
  if (confidence === 'none') return 'rgba(100,116,139,0.25)'
  const base: Record<string, string> = {
    A: '#22c55e',
    B: '#3b82f6',
    C: '#eab308',
    D: '#ef4444',
  }
  const color = base[tier] ?? 'rgba(100,116,139,0.25)'
  if (confidence === 'low') return color + '66'
  return color + '33'
}

function tierTextColor(tier: string, confidence: string): string {
  if (confidence === 'none') return 'var(--text-muted)'
  const base: Record<string, string> = {
    A: '#22c55e',
    B: '#3b82f6',
    C: '#eab308',
    D: '#ef4444',
  }
  const color = base[tier] ?? 'var(--text-muted)'
  if (confidence === 'low') return color + 'aa'
  return color
}

function tierMultiplier(tier: string, confidence: string): string {
  if (confidence === 'none') return '—'
  if (confidence === 'low') return '1.0×'
  const m: Record<string, string> = { A: '1.2×', B: '1.0×', C: '0.8×', D: '0.0×' }
  return m[tier] ?? '—'
}

// Build symbol-indexed lookup: symbol → strategy → score
function buildLookup(data: LiquidityData): Record<string, Record<string, LiquidityScore>> {
  const map: Record<string, Record<string, LiquidityScore>> = {}
  for (const [strat, rows] of Object.entries(data.strategies)) {
    for (const row of rows) {
      if (!map[row.symbol]) map[row.symbol] = {}
      map[row.symbol][strat] = row
    }
  }
  return map
}

function allSymbols(data: LiquidityData): string[] {
  const set = new Set<string>()
  for (const rows of Object.values(data.strategies)) {
    for (const r of rows) set.add(r.symbol)
  }
  // Sort by best average tier (A best)
  return Array.from(set).sort((a, b) => a.localeCompare(b))
}

// ── Component ─────────────────────────────────────────────────────────────────

export function LiquidityHeatmap({ onSymbolClick }: Props) {
  const [data, setData] = useState<LiquidityData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tooltip, setTooltip] = useState<{
    x: number; y: number; content: string
  } | null>(null)

  useEffect(() => {
    fetch('/api/research/liquidity/scores', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then((d) => { setData(d); setLoading(false) })
      .catch((e) => { setError(String(e)); setLoading(false) })
  }, [])

  if (loading) return <div style={{ color: 'var(--text-muted)' }} className="py-8 text-center">Loading liquidity data…</div>
  if (error) return <div style={{ color: 'var(--red)' }} className="py-4">Error: {error}</div>
  if (!data) return null

  const lookup = buildLookup(data)
  const symbols = allSymbols(data)

  if (symbols.length === 0) {
    return (
      <div className="py-12 text-center" style={{ color: 'var(--text-muted)' }}>
        No liquidity data yet. Run the weekly research scan to populate.
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
            maxWidth: 220,
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
                const bg = tierColor(cell.tier, cell.confidence)
                const fg = tierTextColor(cell.tier, cell.confidence)
                const mult = tierMultiplier(cell.tier, cell.confidence)
                return (
                  <td
                    key={col.key}
                    className="py-2 px-3 text-center"
                    onMouseEnter={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect()
                      const score = cell.composite_score != null
                        ? cell.composite_score.toFixed(3)
                        : 'n/a'
                      setTooltip({
                        x: rect.left,
                        y: rect.top,
                        content: `Tier ${cell.tier} · ${cell.confidence}\nScore: ${score}\nMultiplier: ${mult}\nUpdated: ${(cell as any).last_updated?.slice(0, 10) ?? '—'}`,
                      })
                    }}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    <span
                      className="inline-block rounded px-2 py-0.5 font-mono font-semibold text-xs"
                      style={{ backgroundColor: bg, color: fg }}
                    >
                      {cell.tier}
                    </span>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-3 flex gap-4 text-xs" style={{ color: 'var(--text-muted)' }}>
        {['A', 'B', 'C', 'D'].map((t) => (
          <span key={t} className="flex items-center gap-1">
            <span
              className="inline-block w-3 h-3 rounded-sm"
              style={{ backgroundColor: tierColor(t, 'high') }}
            />
            Tier {t} · {tierMultiplier(t, 'high')}
          </span>
        ))}
        <span className="flex items-center gap-1">
          <span
            className="inline-block w-3 h-3 rounded-sm"
            style={{ backgroundColor: 'rgba(100,116,139,0.25)' }}
          />
          No data
        </span>
      </div>
    </div>
  )
}
