import { useEffect, useRef, useState } from 'react'
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartTooltip,
  XAxis,
  YAxis,
} from 'recharts'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SymbolStat {
  symbol: string
  strategy_type: string
  trade_count: number
  win_count: number
  win_rate: number
  avg_pnl_per_trade: number
  total_pnl: number
  max_drawdown: number
  sharpe_ratio: number | null
  confidence: string
  date_range_start: string | null
  date_range_end: string | null
}

interface BacktestTrade {
  trade_id: number
  symbol: string
  strategy_type: string
  entry_date: string
  exit_date: string | null
  entry_credit: number | null
  exit_debit: number | null
  pnl: number | null
  exit_reason: string | null
  entry_delta: number | null
  entry_ivr: number | null
  entry_regime: string | null
  holding_days: number | null
}

interface SymbolDeepDiveData {
  symbol: string
  stats: Record<string, SymbolStat>
  recent_trades: Record<string, BacktestTrade[]>
}

// Liquidity score shape from /api/research/liquidity/scores
interface LiqScore {
  symbol: string
  strategy_type: string
  tier: string
  confidence: string
  composite_score: number | null
}

interface Props {
  symbol: string
  onClose: () => void
  liquidityScores: LiqScore[]  // pre-filtered to this symbol from parent
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STRATEGIES = [
  { key: 'wheel_csp', label: 'Wheel CSP' },
  { key: 'bull_put_spread', label: 'Bull Put Spread' },
  { key: 'bear_call_spread', label: 'Bear Call Spread' },
  { key: 'iron_condor', label: 'Iron Condor' },
  { key: 'long_call_vertical', label: 'Long Call Vertical' },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt$(n: number | null | undefined, dec = 0): string {
  if (n == null) return '—'
  return '$' + (n >= 0 ? '+' : '') + n.toFixed(dec)
}

function confidenceBadgeStyle(c: string): React.CSSProperties {
  if (c === 'high') return { backgroundColor: 'rgba(34,197,94,0.20)', color: '#22c55e' }
  if (c === 'low')  return { backgroundColor: 'rgba(234,179,8,0.18)',  color: '#eab308' }
  return { backgroundColor: 'rgba(100,116,139,0.18)', color: 'var(--text-muted)' }
}

function liqTierStyle(tier: string): React.CSSProperties {
  const m: Record<string, string> = {
    A: '#22c55e', B: '#3b82f6', C: '#eab308', D: '#ef4444',
  }
  const c = m[tier] ?? 'var(--text-muted)'
  return { backgroundColor: c + '33', color: c }
}

function buildEquityCurve(trades: BacktestTrade[]): { date: string; cum: number }[] {
  const sorted = [...trades].sort((a, b) => a.entry_date.localeCompare(b.entry_date))
  let cum = 0
  return sorted
    .filter((t) => t.pnl != null)
    .map((t) => {
      cum += t.pnl!
      return { date: t.entry_date.slice(0, 10), cum: parseFloat(cum.toFixed(2)) }
    })
}

// ── Strategy tile ─────────────────────────────────────────────────────────────

function StrategyTile({
  label,
  stat,
  trades,
}: {
  label: string
  stat: SymbolStat | undefined
  trades: BacktestTrade[]
}) {
  const [expanded, setExpanded] = useState(false)

  if (!stat) {
    return (
      <div
        className="rounded-md p-3"
        style={{
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border)',
          opacity: 0.5,
        }}
      >
        <div className="text-xs font-semibold mb-1" style={{ color: 'var(--text-secondary)' }}>
          {label}
        </div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
          No data
        </div>
      </div>
    )
  }

  const equityCurve = expanded ? buildEquityCurve(trades) : []

  return (
    <div
      className="rounded-md overflow-hidden"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      {/* Tile header (always visible) */}
      <button
        className="w-full text-left p-3 hover:bg-white/5 transition-colors"
        onClick={() => setExpanded((prev) => !prev)}
      >
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs font-semibold" style={{ color: 'var(--text-secondary)' }}>
            {label}
          </span>
          <span
            className="text-[10px] rounded px-1.5 py-0.5 font-semibold"
            style={confidenceBadgeStyle(stat.confidence)}
          >
            {stat.confidence}
          </span>
        </div>
        <div className="flex items-end gap-3">
          <div>
            <div
              className="text-lg font-mono font-bold tabular"
              style={{
                color:
                  stat.win_rate >= 0.60 ? 'var(--green)' :
                  stat.win_rate >= 0.50 ? 'var(--yellow)' :
                  stat.win_rate < 0.30  ? '#fca5a5' : 'var(--text-primary)',
              }}
            >
              {(stat.win_rate * 100).toFixed(0)}%
            </div>
            <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
              {stat.trade_count} trades
            </div>
          </div>
          <div className="text-right ml-auto">
            <div
              className="text-sm font-mono tabular"
              style={{ color: stat.avg_pnl_per_trade >= 0 ? 'var(--green)' : 'var(--red)' }}
            >
              {fmt$(stat.avg_pnl_per_trade, 0)}
            </div>
            <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>avg/trade</div>
          </div>
        </div>
        <div className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
          {expanded ? '▲ collapse' : '▼ show trades'}
        </div>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div style={{ borderTop: '1px solid var(--border)' }}>
          {/* Equity curve */}
          {equityCurve.length > 1 && (
            <div className="px-3 pt-3" style={{ height: 80 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={equityCurve}>
                  <XAxis dataKey="date" hide />
                  <YAxis hide />
                  <RechartTooltip
                    contentStyle={{
                      backgroundColor: 'var(--bg-card)',
                      border: '1px solid var(--border)',
                      fontSize: 11,
                    }}
                    formatter={(v: number) => [`$${v.toFixed(0)}`, 'Cumulative P&L']}
                  />
                  <Line
                    type="monotone"
                    dataKey="cum"
                    stroke="var(--accent)"
                    strokeWidth={1.5}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Trade table */}
          {trades.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    <th className="py-1.5 px-3 text-left" style={{ color: 'var(--text-muted)' }}>Entry</th>
                    <th className="py-1.5 px-3 text-left" style={{ color: 'var(--text-muted)' }}>Exit</th>
                    <th className="py-1.5 px-3 text-right" style={{ color: 'var(--text-muted)' }}>P&L</th>
                    <th className="py-1.5 px-3 text-left" style={{ color: 'var(--text-muted)' }}>Regime</th>
                    <th className="py-1.5 px-3 text-left" style={{ color: 'var(--text-muted)' }}>Exit reason</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr
                      key={t.trade_id}
                      style={{ borderBottom: '1px solid var(--border)' }}
                    >
                      <td className="py-1.5 px-3 font-mono" style={{ color: 'var(--text-secondary)' }}>
                        {t.entry_date.slice(0, 10)}
                      </td>
                      <td className="py-1.5 px-3 font-mono" style={{ color: 'var(--text-muted)' }}>
                        {t.exit_date?.slice(0, 10) ?? '—'}
                      </td>
                      <td
                        className="py-1.5 px-3 text-right font-mono tabular"
                        style={{ color: (t.pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}
                      >
                        {t.pnl != null ? fmt$(t.pnl, 0) : '—'}
                      </td>
                      <td className="py-1.5 px-3" style={{ color: 'var(--text-muted)' }}>
                        {t.entry_regime ?? '—'}
                      </td>
                      <td className="py-1.5 px-3" style={{ color: 'var(--text-muted)' }}>
                        {t.exit_reason ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-muted)' }}>
              No trade records available.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function SymbolDeepDive({ symbol, onClose, liquidityScores }: Props) {
  const [data, setData] = useState<SymbolDeepDiveData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetch(`/api/research/winrate/symbol/${encodeURIComponent(symbol)}`, {
      credentials: 'same-origin',
    })
      .then((r) => {
        if (r.status === 404) return null
        if (!r.ok) return Promise.reject(r.statusText)
        return r.json()
      })
      .then((d) => { setData(d); setLoading(false) })
      .catch((e) => { setError(String(e)); setLoading(false) })
  }, [symbol])

  // Close on backdrop click
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose()
  }

  // Liquidity data for this symbol
  const liqByStrategy: Record<string, LiqScore> = {}
  for (const row of liquidityScores) {
    if (row.symbol === symbol) liqByStrategy[row.strategy_type] = row
  }

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-40 flex justify-end"
      style={{ backgroundColor: 'rgba(0,0,0,0.55)' }}
      onClick={handleBackdropClick}
    >
      <div
        className="h-full overflow-y-auto shadow-2xl"
        style={{
          width: 540,
          backgroundColor: 'var(--bg-secondary)',
          borderLeft: '1px solid var(--border)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="sticky top-0 z-10 flex items-center justify-between px-5 py-4"
          style={{
            backgroundColor: 'var(--bg-secondary)',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div>
            <h2 className="text-lg font-bold font-mono" style={{ color: 'var(--text-primary)' }}>
              {symbol}
            </h2>
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
              Research deep dive
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-2xl leading-none hover:opacity-70 transition-opacity"
            style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
          >
            ×
          </button>
        </div>

        <div className="px-5 py-4 space-y-6">
          {/* ── Liquidity section ─────────────────────────────────────────── */}
          <section>
            <h3 className="section-heading">Liquidity Scores</h3>
            {Object.keys(liqByStrategy).length === 0 ? (
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                No liquidity data for {symbol}. Run the weekly scan.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {STRATEGIES.map(({ key, label }) => {
                  const row = liqByStrategy[key]
                  if (!row) return null
                  return (
                    <div
                      key={key}
                      className="rounded-md p-3"
                      style={{
                        backgroundColor: 'var(--bg-card)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      <div className="text-[11px] mb-1.5" style={{ color: 'var(--text-muted)' }}>
                        {label}
                      </div>
                      <div className="flex items-center gap-2">
                        <span
                          className="text-sm font-mono font-bold rounded px-1.5 py-0.5"
                          style={liqTierStyle(row.tier)}
                        >
                          Tier {row.tier}
                        </span>
                        <span
                          className="text-[10px] rounded px-1 py-0.5"
                          style={confidenceBadgeStyle(row.confidence)}
                        >
                          {row.confidence}
                        </span>
                      </div>
                      {row.composite_score != null && (
                        <div className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
                          Score: {row.composite_score.toFixed(3)}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          {/* ── Win-rate / backtest section ──────────────────────────────── */}
          <section>
            <h3 className="section-heading">Historical Backtest Performance</h3>

            {loading && (
              <div className="py-6 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
                Loading backtest data…
              </div>
            )}

            {!loading && error && (
              <div className="text-xs" style={{ color: 'var(--red)' }}>
                Error loading backtest data: {error}
              </div>
            )}

            {!loading && !error && !data && (
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                No backtest data for {symbol}. The weekly sweep has not yet processed this symbol.
              </p>
            )}

            {!loading && !error && data && (
              <div className="space-y-3">
                {STRATEGIES.map(({ key, label }) => (
                  <StrategyTile
                    key={key}
                    label={label}
                    stat={data.stats[key]}
                    trades={data.recent_trades[key] ?? []}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
