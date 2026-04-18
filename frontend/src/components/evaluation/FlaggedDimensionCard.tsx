import { useState } from 'react'
import type { FlaggedDecisionGroup } from '../../api/client'

interface FlaggedDimensionCardProps {
  flag: FlaggedDecisionGroup
}

function severityColor(severity: string): string {
  const s = severity.toLowerCase()
  if (s === 'high') return 'var(--red)'
  if (s === 'medium') return 'var(--yellow)'
  return 'var(--text-muted)'
}

function dimensionLabel(dim: string): string {
  return dim.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

interface DimensionScore {
  dimension: string
  score: number
  justification?: string
}

function getJudgeScores(decision: FlaggedDecisionGroup['decisions'][number]): DimensionScore[] {
  for (const score of decision.scores ?? []) {
    if (score.scorer_type === 'judge' && score.dimension_scores) {
      return score.dimension_scores
    }
  }
  return []
}

export function FlaggedDimensionCard({ flag }: FlaggedDimensionCardProps) {
  const [expanded, setExpanded] = useState(false)
  const color = severityColor(flag.severity)

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ border: `1px solid color-mix(in srgb, ${color} 30%, var(--border))` }}
    >
      {/* Header — click to expand */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left px-4 py-3 flex items-center justify-between gap-3"
        style={{
          backgroundColor: `color-mix(in srgb, ${color} 8%, var(--bg-card))`,
          border: 'none',
          cursor: 'pointer',
        }}
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span
            className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider shrink-0"
            style={{
              backgroundColor: `color-mix(in srgb, ${color} 18%, transparent)`,
              color,
              border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
            }}
          >
            {flag.severity}
          </span>
          <span className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
            {dimensionLabel(flag.dimension)}
          </span>
          {flag.strategy && (
            <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)' }}>
              · {flag.strategy}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {flag.decisions.length} decision{flag.decisions.length !== 1 ? 's' : ''}
          </span>
          <span style={{ color: 'var(--text-muted)', fontSize: 12, transform: expanded ? 'rotate(180deg)' : 'none', display: 'inline-block', transition: 'transform 0.15s' }}>
            ▼
          </span>
        </div>
      </button>

      {/* Flag reason */}
      <div
        className="px-4 py-2 text-xs"
        style={{
          backgroundColor: 'var(--bg-card)',
          borderBottom: expanded ? `1px solid var(--border)` : 'none',
          color: 'var(--text-secondary)',
        }}
      >
        {flag.reason}
      </div>

      {/* Expanded decisions */}
      {expanded && (
        <div style={{ backgroundColor: 'var(--bg-secondary)' }}>
          {flag.decisions.length === 0 ? (
            <div className="px-4 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
              No decision details available.
            </div>
          ) : (
            flag.decisions.map((d, i) => {
              const judgeScores = getJudgeScores(d)
              const dimScore = judgeScores.find((s) => s.dimension === flag.dimension)
              const reasoning = typeof d.reasoning === 'object' && d.reasoning !== null
                ? (d.reasoning as Record<string, unknown>)
                : null

              return (
                <div
                  key={d.id ?? i}
                  className="px-4 py-3"
                  style={{ borderBottom: i < flag.decisions.length - 1 ? '1px solid var(--border)' : 'none' }}
                >
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="text-xs font-mono font-semibold" style={{ color: 'var(--text-primary)' }}>
                      {d.underlying ?? '—'}
                    </span>
                    <span className="text-xs uppercase" style={{ color: 'var(--text-muted)' }}>
                      {d.action ?? '—'}
                    </span>
                    <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                      {d.timestamp ? new Date(d.timestamp).toLocaleDateString() : ''}
                    </span>
                    {dimScore != null && (
                      <span
                        className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
                        style={{
                          backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)`,
                          color,
                        }}
                      >
                        score {dimScore.score.toFixed(2)}
                      </span>
                    )}
                  </div>

                  {/* Reasoning summary */}
                  {reasoning && (
                    <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>
                      {String(
                        reasoning['summary'] ??
                        reasoning['decision_rationale'] ??
                        reasoning['rationale'] ??
                        ''
                      ).slice(0, 200) || null}
                    </div>
                  )}

                  {/* Judge justification for this dimension */}
                  {dimScore?.justification && (
                    <div
                      className="text-xs rounded px-2 py-1.5 mt-1"
                      style={{
                        backgroundColor: `color-mix(in srgb, ${color} 8%, var(--bg-card))`,
                        color: 'var(--text-secondary)',
                        fontStyle: 'italic',
                      }}
                    >
                      Judge: {dimScore.justification}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
