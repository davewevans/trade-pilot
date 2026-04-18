import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type EvaluationSummary } from '../api/client'
import { ReviewStatusBadge } from '../components/evaluation/ReviewStatusBadge'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'

const MINIMUM_SAMPLE_SIZE = 15

export function Evaluations() {
  const navigate = useNavigate()
  const [evaluations, setEvaluations] = useState<EvaluationSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    api.listEvaluations(50, 0)
      .then((d) => {
        setEvaluations(d.evaluations)
        setTotal(d.total)
      })
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="p-5 max-w-screen-lg mx-auto">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-2xl font-bold mb-1" style={{ color: 'var(--text-primary)' }}>
          Monthly Evaluations
        </h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Automated monthly scoring of Claude's trading decisions using the decision rubric.
        </p>
      </div>

      {loading && <LoadingSpinner />}

      {error && (
        <div
          className="rounded-md px-4 py-3 text-sm"
          style={{ backgroundColor: 'color-mix(in srgb, var(--red) 12%, var(--bg-card))', color: 'var(--red)', border: '1px solid color-mix(in srgb, var(--red) 30%, var(--border))' }}
        >
          Failed to load evaluations: {error}
        </div>
      )}

      {!loading && !error && evaluations.length === 0 && (
        <div
          className="rounded-lg p-8 text-center"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          <div className="text-lg font-medium mb-2" style={{ color: 'var(--text-primary)' }}>
            No monthly evaluations yet
          </div>
          <div className="text-sm" style={{ color: 'var(--text-muted)' }}>
            The first evaluation runs on the 1st of next month.
          </div>
        </div>
      )}

      {!loading && !error && evaluations.length > 0 && (
        <div
          className="rounded-lg overflow-hidden"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          {/* Table header */}
          <div
            className="grid text-[11px] uppercase tracking-wider font-semibold px-4 py-2"
            style={{
              gridTemplateColumns: '120px 1fr 1fr 80px 80px 160px',
              color: 'var(--text-muted)',
              borderBottom: '1px solid var(--border)',
            }}
          >
            <div>Month</div>
            <div>Decisions</div>
            <div>Closed Trades</div>
            <div>Flags</div>
            <div>Disagree %</div>
            <div>Status</div>
          </div>

          {evaluations.map((ev, i) => {
            const lowSample = ev.decisions_scored < MINIMUM_SAMPLE_SIZE
            return (
              <div
                key={ev.month}
                className="grid items-center px-4 py-3 cursor-pointer transition-colors"
                style={{
                  gridTemplateColumns: '120px 1fr 1fr 80px 80px 160px',
                  borderBottom: i < evaluations.length - 1 ? '1px solid var(--border)' : 'none',
                  backgroundColor: 'transparent',
                }}
                onMouseEnter={(e) => {
                  ;(e.currentTarget as HTMLDivElement).style.backgroundColor =
                    'color-mix(in srgb, var(--accent) 5%, var(--bg-card))'
                }}
                onMouseLeave={(e) => {
                  ;(e.currentTarget as HTMLDivElement).style.backgroundColor = 'transparent'
                }}
                onClick={() => navigate(`/evaluations/${ev.month}`)}
              >
                {/* Month */}
                <div className="text-sm font-semibold font-mono" style={{ color: 'var(--text-primary)' }}>
                  {ev.month}
                </div>

                {/* Decisions scored */}
                <div className="flex items-center gap-1.5">
                  <span className="text-sm tabular-nums" style={{ color: 'var(--text-primary)' }}>
                    {ev.decisions_scored}
                  </span>
                  {lowSample && (
                    <span
                      title="Insufficient sample"
                      className="text-[10px] px-1.5 py-0.5 rounded font-semibold"
                      style={{
                        backgroundColor: 'color-mix(in srgb, var(--yellow) 15%, transparent)',
                        color: 'var(--yellow)',
                      }}
                    >
                      Low
                    </span>
                  )}
                </div>

                {/* Closed trades */}
                <div className="text-sm tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                  {ev.closed_trades_in_window ?? '—'}
                </div>

                {/* Flags */}
                <div>
                  {ev.flag_count > 0 ? (
                    <span
                      className="text-sm font-semibold tabular-nums"
                      style={{ color: 'var(--red)' }}
                    >
                      {ev.flag_count}
                    </span>
                  ) : (
                    <span className="text-sm" style={{ color: 'var(--text-muted)' }}>—</span>
                  )}
                </div>

                {/* Judge–operator disagreement */}
                <div className="text-sm tabular-nums" style={{ color: 'var(--text-secondary)' }}>
                  {ev.judge_operator_disagreement != null
                    ? `${(ev.judge_operator_disagreement * 100).toFixed(0)}%`
                    : '—'}
                </div>

                {/* Review status */}
                <div>
                  <ReviewStatusBadge status={ev.review_status} />
                </div>
              </div>
            )
          })}

          {total > evaluations.length && (
            <div
              className="px-4 py-2 text-xs"
              style={{ color: 'var(--text-muted)', borderTop: '1px solid var(--border)' }}
            >
              Showing {evaluations.length} of {total}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
