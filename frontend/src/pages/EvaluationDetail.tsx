import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  api,
  type EvaluationDetailResponse,
  type FlaggedDecisionGroup,
  type FlaggedDecisionsResponse,
  type MarkReviewedResponse,
} from '../api/client'
import { FlaggedDimensionCard } from '../components/evaluation/FlaggedDimensionCard'
import { SpotCheckQueue } from '../components/evaluation/SpotCheckQueue'
import { InsufficientSampleBanner } from '../components/evaluation/InsufficientSampleBanner'
import { ReviewStatusBadge } from '../components/evaluation/ReviewStatusBadge'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import { StatCard } from '../components/shared/StatCard'
import { useCanMutate } from '../context/AuthContext'

// ── Colour palette for prompt version bars ────────────────────────────────────

const PV_COLORS = [
  'var(--accent)',
  'var(--green)',
  'var(--yellow)',
  'var(--blue)',
  '#a855f7',
  '#f97316',
]

// ── Mark-reviewed dialog ──────────────────────────────────────────────────────

interface MarkReviewedDialogProps {
  month: string
  onClose: () => void
  onSuccess: (result: MarkReviewedResponse) => void
}

function MarkReviewedDialog({ month, onClose, onSuccess }: MarkReviewedDialogProps) {
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await api.markReviewed(month, note.trim() || null)
      onSuccess(result)
    } catch (e) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)' }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="rounded-lg p-6 max-w-md w-full space-y-4 mx-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <div className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
          Mark {month} as Reviewed
        </div>
        <div>
          <label className="block text-xs mb-1.5 font-medium" style={{ color: 'var(--text-secondary)' }}>
            Action note (optional)
          </label>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Describe any follow-up actions taken…"
            rows={3}
            className="w-full rounded px-3 py-2 text-sm resize-none"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
              outline: 'none',
            }}
          />
        </div>
        {error && (
          <div className="text-xs" style={{ color: 'var(--red)' }}>{error}</div>
        )}
        <div className="flex gap-2 justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded text-sm"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border)',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={loading}
            className="px-4 py-2 rounded text-sm font-medium"
            style={{
              backgroundColor: loading ? 'var(--text-muted)' : 'var(--accent)',
              color: '#fff',
              border: 'none',
              cursor: loading ? 'default' : 'pointer',
            }}
          >
            {loading ? 'Saving…' : note.trim() ? 'Mark reviewed + action' : 'Mark reviewed'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Score distribution chart for one strategy ─────────────────────────────────

interface StrategyChartProps {
  strategy: string
  byDimension: Record<string, {
    mean: number
    n: number
    by_prompt_version?: Record<string, { mean: number; n: number }>
  }>
}

function StrategyChart({ strategy, byDimension }: StrategyChartProps) {
  // Collect all prompt versions seen across dimensions
  const pvSet = new Set<string>()
  for (const dim of Object.values(byDimension)) {
    for (const pv of Object.keys(dim.by_prompt_version ?? {})) {
      pvSet.add(pv)
    }
  }
  const pvList = Array.from(pvSet).sort()
  const multiPv = pvList.length > 1

  // Build chart data: one entry per dimension
  const chartData = Object.entries(byDimension).map(([dim, stats]) => {
    const entry: Record<string, unknown> = {
      dimension: dim.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
      overall: Number(stats.mean.toFixed(3)),
    }
    if (multiPv) {
      for (const pv of pvList) {
        entry[pv] = stats.by_prompt_version?.[pv]?.mean != null
          ? Number(stats.by_prompt_version[pv].mean.toFixed(3))
          : undefined
      }
    }
    return entry
  })

  const strategyLabel = strategy.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

  return (
    <div
      className="rounded-lg p-4"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      <div className="text-sm font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
        {strategyLabel}
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={chartData} margin={{ top: 8, right: 8, left: -20, bottom: 50 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="dimension"
            tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
            angle={-35}
            textAnchor="end"
            interval={0}
          />
          <YAxis
            domain={[0, 1]}
            tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
            tickFormatter={(v) => v.toFixed(1)}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--bg-card)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              fontSize: 12,
              color: 'var(--text-primary)',
            }}
            formatter={(value: number) => value.toFixed(3)}
          />
          {multiPv ? (
            <>
              <Legend
                wrapperStyle={{ fontSize: 10, color: 'var(--text-secondary)', paddingTop: 8 }}
              />
              {pvList.map((pv, idx) => (
                <Bar
                  key={pv}
                  dataKey={pv}
                  name={pv}
                  fill={PV_COLORS[idx % PV_COLORS.length]}
                  radius={[2, 2, 0, 0]}
                />
              ))}
            </>
          ) : (
            <Bar dataKey="overall" name="Mean score" radius={[2, 2, 0, 0]}>
              {chartData.map((entry) => {
                const val = entry.overall as number
                const color = val >= 0.7 ? 'var(--green)' : val >= 0.5 ? 'var(--yellow)' : 'var(--red)'
                return <Cell key={String(entry.dimension)} fill={color} />
              })}
              <LabelList
                dataKey="overall"
                position="top"
                style={{ fill: 'var(--text-muted)', fontSize: 9 }}
                formatter={(v: number) => v.toFixed(2)}
              />
            </Bar>
          )}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export function EvaluationDetail() {
  const canMutate = useCanMutate()
  const { month } = useParams<{ month: string }>()
  const navigate = useNavigate()

  const [detail, setDetail] = useState<EvaluationDetailResponse | null>(null)
  const [flagged, setFlagged] = useState<FlaggedDecisionGroup[]>([])
  const [loadingDetail, setLoadingDetail] = useState(true)
  const [loadingFlags, setLoadingFlags] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showReviewDialog, setShowReviewDialog] = useState(false)

  useEffect(() => {
    if (!month) return
    setLoadingDetail(true)
    api.getEvaluation(month)
      .then((d) => setDetail(d))
      .catch((e) => setError(String((e as Error)?.message ?? e)))
      .finally(() => setLoadingDetail(false))

    setLoadingFlags(true)
    api.getFlaggedDecisions(month)
      .then((d: FlaggedDecisionsResponse) => setFlagged(d.flags))
      .catch(() => {})
      .finally(() => setLoadingFlags(false))
  }, [month])

  const handleReviewSuccess = (result: MarkReviewedResponse) => {
    setDetail((prev) =>
      prev
        ? {
            ...prev,
            review_status: result.review_status,
            reviewed_at: result.reviewed_at,
            action_note: result.action_note,
          }
        : prev,
    )
    setShowReviewDialog(false)
  }

  if (loadingDetail) return <LoadingSpinner />

  if (error) {
    return (
      <div className="p-5">
        <div
          className="rounded-md px-4 py-3 text-sm"
          style={{ backgroundColor: 'color-mix(in srgb, var(--red) 12%, var(--bg-card))', color: 'var(--red)', border: '1px solid color-mix(in srgb, var(--red) 30%, var(--border))' }}
        >
          {error}
        </div>
      </div>
    )
  }

  if (!detail) return null

  const scoreDistribution = detail.score_distribution ?? {}
  const byStrategy = scoreDistribution.by_strategy ?? {}
  const hasFlags = detail.flag_summary.length > 0

  return (
    <div className="p-5 max-w-screen-lg mx-auto">
      {/* Insufficient sample banner — above everything */}
      <InsufficientSampleBanner decisionsScored={detail.decisions_scored} />

      {/* Back nav */}
      <button
        onClick={() => navigate('/evaluations')}
        className="text-xs mb-4 flex items-center gap-1"
        style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
      >
        ← All evaluations
      </button>

      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-5 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold mb-1 font-mono" style={{ color: 'var(--text-primary)' }}>
            {month}
          </h1>
          <div className="flex items-center gap-2 flex-wrap">
            <ReviewStatusBadge status={detail.review_status} />
            {detail.reviewed_at && (
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                reviewed {new Date(detail.reviewed_at).toLocaleDateString()}
              </span>
            )}
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              generated {new Date(detail.generated_at).toLocaleDateString()}
            </span>
          </div>
          {detail.action_note && (
            <div
              className="mt-2 text-xs rounded px-2 py-1 inline-block"
              style={{ backgroundColor: 'color-mix(in srgb, var(--accent) 10%, var(--bg-card))', color: 'var(--text-secondary)', border: '1px solid color-mix(in srgb, var(--accent) 25%, var(--border))' }}
            >
              Action: {detail.action_note}
            </div>
          )}
        </div>
        {canMutate && detail.review_status === 'pending' && (
          <button
            onClick={() => setShowReviewDialog(true)}
            className="px-4 py-2 rounded text-sm font-medium shrink-0"
            style={{
              backgroundColor: 'var(--accent)',
              color: '#fff',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            Mark reviewed
          </button>
        )}
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 mb-6 sm:grid-cols-4">
        <StatCard
          label="Decisions scored"
          value={detail.decisions_scored}
          size="md"
        />
        <StatCard
          label="Closed trades"
          value={detail.score_distribution?.overall?.closed_trades_in_window ?? '—'}
          size="md"
        />
        <StatCard
          label="Flags"
          value={detail.flag_summary.length}
          size="md"
          valueColor={detail.flag_summary.length > 0 ? 'var(--red)' : 'var(--green)'}
        />
        <StatCard
          label="Judge–op disagree"
          value={
            detail.judge_operator_disagreement != null
              ? `${(detail.judge_operator_disagreement * 100).toFixed(0)}%`
              : '—'
          }
          size="md"
        />
      </div>

      {/* Score distribution charts */}
      {Object.keys(byStrategy).length > 0 && (
        <section className="mb-6">
          <h2 className="text-base font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
            Score distribution by strategy
          </h2>
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))' }}>
            {Object.entries(byStrategy).map(([strategy, data]) => (
              <StrategyChart
                key={strategy}
                strategy={strategy}
                byDimension={data.by_dimension}
              />
            ))}
          </div>
        </section>
      )}

      {/* Flagged dimensions */}
      {hasFlags && (
        <section className="mb-6">
          <h2 className="text-base font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
            Flagged dimensions
          </h2>
          {loadingFlags ? (
            <LoadingSpinner />
          ) : (
            <div className="flex flex-col gap-3">
              {flagged.length > 0
                ? flagged.map((flag, i) => (
                    <FlaggedDimensionCard key={`${flag.strategy}-${flag.dimension}-${i}`} flag={flag} />
                  ))
                : detail.flag_summary.map((flag, i) => (
                    // Fallback: show flag summary without decisions
                    <div
                      key={i}
                      className="rounded-lg px-4 py-3"
                      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <span
                          className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded"
                          style={{
                            color: flag.severity === 'high' ? 'var(--red)' : 'var(--yellow)',
                            backgroundColor: `color-mix(in srgb, ${flag.severity === 'high' ? 'var(--red)' : 'var(--yellow)'} 15%, transparent)`,
                          }}
                        >
                          {flag.severity}
                        </span>
                        <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                          {flag.dimension.replace(/_/g, ' ')}
                        </span>
                        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>· {flag.strategy}</span>
                      </div>
                      <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>{flag.reason}</div>
                    </div>
                  ))}
            </div>
          )}
        </section>
      )}

      {/* Spot-check queue */}
      <section className="mb-6">
        <h2 className="text-base font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
          Spot-check queue
        </h2>
        <SpotCheckQueue month={month!} />
      </section>

      {/* Mark reviewed dialog */}
      {showReviewDialog && (
        <MarkReviewedDialog
          month={month!}
          onClose={() => setShowReviewDialog(false)}
          onSuccess={handleReviewSuccess}
        />
      )}
    </div>
  )
}
