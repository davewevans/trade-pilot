import { useEffect, useState } from 'react'
import { api, type ScorecardResponse, type ScorecardCellResponse, type ResearchLastRunResponse } from '../../api/client'
import { StalenessBadge } from '../shared/StalenessBadge'

// ── Cell component ────────────────────────────────────────────────────────────

interface CellProps {
  title: string
  cell: ScorecardCellResponse | undefined
  isLive: boolean
  windowDays: number
}

function ScorecardCell({ title, cell, isLive, windowDays }: CellProps) {
  const [showSymbols, setShowSymbols] = useState(false)

  const isEmpty = !cell || cell.count === 0
  const isThin = cell && cell.count > 0 && cell.count < 3

  const borderStyle = isLive
    ? '2px solid var(--accent)'
    : '2px dashed var(--text-muted)'

  const badgeStyle: React.CSSProperties = isLive
    ? {
        backgroundColor: 'var(--green)',
        color: 'white',
        padding: '1px 6px',
        borderRadius: 3,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: '0.05em',
        textTransform: 'uppercase' as const,
      }
    : {
        border: '1px dashed var(--text-muted)',
        color: 'var(--text-muted)',
        padding: '1px 6px',
        borderRadius: 3,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: '0.05em',
        textTransform: 'uppercase' as const,
      }

  return (
    <div
      className="p-4 rounded-lg"
      style={{
        backgroundColor: 'var(--bg-card)',
        border: borderStyle,
        opacity: isThin ? 0.85 : 1,
      }}
    >
      <div className="flex items-start justify-between mb-2 gap-2">
        <div className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
          {title}
        </div>
        <span style={badgeStyle}>
          {isLive ? 'LIVE DATA' : 'PROXY EST.'}
        </span>
      </div>

      {isEmpty ? (
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
          No data yet — recommendations need {windowDays} days of aging before outcomes are computed
        </p>
      ) : (
        <>
          <div className="space-y-1.5">
            <div className="flex justify-between text-sm">
              <span style={{ color: 'var(--text-muted)' }}>Count</span>
              <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{cell!.count}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span style={{ color: 'var(--text-muted)' }}>Correct %</span>
              <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                {cell!.recommender_correct_pct != null
                  ? `${cell!.recommender_correct_pct.toFixed(1)}%`
                  : '—'}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span style={{ color: 'var(--text-muted)' }}>Avg P&amp;L/trade</span>
              <span
                style={{
                  color:
                    cell!.avg_pnl_per_trade == null
                      ? 'var(--text-muted)'
                      : cell!.avg_pnl_per_trade >= 0
                      ? 'var(--green)'
                      : 'var(--red)',
                  fontWeight: 600,
                }}
              >
                {cell!.avg_pnl_per_trade != null
                  ? `$${cell!.avg_pnl_per_trade.toFixed(2)}`
                  : '—'}
              </span>
            </div>
          </div>

          {isThin && (
            <p className="mt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
              Thin data (N={cell!.count}) — not statistically meaningful
            </p>
          )}

          {cell!.symbols.length > 0 && (
            <div className="mt-2">
              <button
                onClick={() => setShowSymbols((s) => !s)}
                className="text-xs"
                style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
              >
                {showSymbols ? 'Hide symbols ▲' : `Symbols (${cell!.symbols.length}) ▼`}
              </button>
              {showSymbols && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {cell!.symbols.map((s) => (
                    <span
                      key={s}
                      className="text-xs font-mono px-1.5 py-0.5 rounded"
                      style={{
                        backgroundColor: 'color-mix(in srgb, var(--accent) 12%, transparent)',
                        color: 'var(--accent)',
                      }}
                    >
                      {s}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  lastRun?: ResearchLastRunResponse | null
}

export function RecommendationScorecard({ lastRun }: Props) {
  const [data, setData] = useState<ScorecardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [windowDays, setWindowDays] = useState(90)

  useEffect(() => {
    setLoading(true)
    api.recommendationScorecard(windowDays, 180)
      .then((d) => { setData(d); setLoading(false) })
      .catch((e) => { setError(String(e)); setLoading(false) })
  }, [windowDays])

  if (loading) {
    return <div className="py-8 text-center text-sm" style={{ color: 'var(--text-muted)' }}>Loading scorecard…</div>
  }
  if (error) {
    return <div className="py-4 text-sm" style={{ color: 'var(--red)' }}>Error: {error}</div>
  }

  const cells = data?.cells

  return (
    <div>
      {/* Header row */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
            Recommendation Accuracy Scorecard
          </h3>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
            How often was the recommender right?
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastRun && (
            <StalenessBadge last_updated={lastRun.most_recent_outcome} label="outcomes" />
          )}
          <div className="flex gap-1">
            {[30, 60, 90].map((d) => (
              <button
                key={d}
                onClick={() => setWindowDays(d)}
                className="px-3 py-1 rounded text-xs font-medium"
                style={{
                  backgroundColor: windowDays === d ? 'var(--accent)' : 'var(--bg-secondary)',
                  color: windowDays === d ? 'white' : 'var(--text-muted)',
                  border: '1px solid var(--border)',
                  cursor: 'pointer',
                }}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 2×2 grid */}
      <div className="grid grid-cols-2 gap-3">
        <ScorecardCell
          title="Accepted Adds"
          cell={cells?.accepted_add}
          isLive={true}
          windowDays={windowDays}
        />
        <ScorecardCell
          title="Rejected Adds"
          cell={cells?.rejected_add}
          isLive={false}
          windowDays={windowDays}
        />
        <ScorecardCell
          title="Accepted Removes"
          cell={cells?.accepted_remove}
          isLive={false}
          windowDays={windowDays}
        />
        <ScorecardCell
          title="Rejected Removes"
          cell={cells?.rejected_remove}
          isLive={true}
          windowDays={windowDays}
        />
      </div>

      {/* Caveat callout */}
      <div
        className="mt-4 p-3 rounded text-xs leading-relaxed"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--yellow) 8%, var(--bg-card))',
          borderLeft: '3px solid var(--yellow)',
          color: 'var(--text-secondary)',
        }}
      >
        Proxy cells are estimates. Ground-truth cells are measurements. Do not compare them as if they're the same unit.
      </div>

      {/* Summary */}
      {data?.summary.caveat && (
        <p className="mt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
          {data.summary.caveat}
        </p>
      )}
    </div>
  )
}
