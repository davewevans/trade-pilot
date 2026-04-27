import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type ResearchLastRunResponse } from '../api/client'
import { StalenessBadge } from '../components/shared/StalenessBadge'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SubScores {
  liq_score?: number
  liq_tier?: string
  wr_tier?: string
  wr_mult?: number
  novelty_bonus?: number
  incumbent_bonus?: number
  [key: string]: unknown
}

interface RecItem {
  recommendation_id?: number
  _key?: string
  symbol: string
  score: number
  reasoning: string
  data_confidence: string
  sub_scores: SubScores
}

interface WatchlistRecs {
  current_members: string[]
  add: RecItem[]
  remove: RecItem[]
  no_change: RecItem[]
  considered_but_rejected: RecItem[]
}

interface RecsResponse {
  generated_at: string
  watchlists: {
    wheel: WatchlistRecs
    iron_condor: WatchlistRecs
    iron_butterfly: WatchlistRecs
    spreads: WatchlistRecs
    calendar_spread: WatchlistRecs
  }
}

type Decision = 'accepted' | 'rejected'

// ── Helpers ───────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const days = Math.floor(diff / 86_400_000)
  if (days === 0) return 'today'
  if (days === 1) return '1 day ago'
  return `${days} days ago`
}

function scoreColor(score: number): string {
  if (score >= 80) return '#22c55e'
  if (score >= 60) return '#3b82f6'
  if (score >= 40) return '#eab308'
  return '#ef4444'
}

function confidenceBadge(conf: string) {
  const colors: Record<string, { bg: string; text: string }> = {
    high: { bg: 'color-mix(in srgb, #22c55e 20%, var(--bg-card))', text: '#22c55e' },
    low: { bg: 'color-mix(in srgb, #eab308 20%, var(--bg-card))', text: '#eab308' },
    none: { bg: 'color-mix(in srgb, #6b7280 20%, var(--bg-card))', text: '#9ca3af' },
  }
  const c = colors[conf] ?? colors['none']
  return (
    <span
      style={{
        fontSize: 11,
        padding: '2px 7px',
        borderRadius: 10,
        backgroundColor: c.bg,
        color: c.text,
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
    >
      {conf}
    </span>
  )
}

// ── Sub-score tooltip ─────────────────────────────────────────────────────────

function SubScoreExpander({ sub }: { sub: SubScores }) {
  const [open, setOpen] = useState(false)
  const entries = Object.entries(sub).filter(([, v]) => v !== undefined && v !== null)
  if (entries.length === 0) return null
  return (
    <span style={{ marginLeft: 6 }}>
      <button
        onClick={() => setOpen((p) => !p)}
        style={{
          fontSize: 10,
          padding: '1px 6px',
          borderRadius: 4,
          border: '1px solid var(--border)',
          background: 'var(--bg-secondary)',
          color: 'var(--text-muted)',
          cursor: 'pointer',
        }}
      >
        {open ? 'hide' : 'details'}
      </button>
      {open && (
        <span
          style={{
            display: 'inline-block',
            marginLeft: 8,
            padding: '4px 8px',
            borderRadius: 4,
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border)',
            fontSize: 11,
            color: 'var(--text-muted)',
            whiteSpace: 'nowrap',
          }}
        >
          {entries.map(([k, v]) => (
            <span key={k} style={{ marginRight: 10 }}>
              <strong>{k}:</strong> {typeof v === 'number' ? v.toFixed(2) : String(v)}
            </span>
          ))}
        </span>
      )}
    </span>
  )
}

// ── Rec row ───────────────────────────────────────────────────────────────────

function RecRow({
  item,
  decision,
  onDecide,
  actionLabel,
}: {
  item: RecItem
  decision: Decision | undefined
  onDecide: (key: string, d: Decision | undefined) => void
  actionLabel: string
}) {
  return (
    <tr
      style={{
        borderBottom: '1px solid var(--border)',
        opacity: decision ? 1 : 0.9,
        backgroundColor: decision === 'accepted'
          ? 'color-mix(in srgb, #22c55e 8%, var(--bg-card))'
          : decision === 'rejected'
          ? 'color-mix(in srgb, #ef4444 8%, var(--bg-card))'
          : 'transparent',
      }}
    >
      <td style={{ padding: '8px 6px', fontWeight: 700, color: 'var(--text-primary)', width: 80 }}>
        {item.symbol}
        <span
          style={{
            marginLeft: 6,
            fontSize: 10,
            padding: '1px 5px',
            borderRadius: 4,
            backgroundColor:
              actionLabel === 'ADD'
                ? 'color-mix(in srgb, #22c55e 20%, var(--bg-card))'
                : 'color-mix(in srgb, #ef4444 20%, var(--bg-card))',
            color: actionLabel === 'ADD' ? '#22c55e' : '#ef4444',
            fontWeight: 700,
          }}
        >
          {actionLabel}
        </span>
      </td>
      <td style={{ padding: '8px 6px', color: scoreColor(item.score), fontWeight: 600, width: 60 }}>
        {item.score.toFixed(1)}
      </td>
      <td style={{ padding: '8px 6px', width: 70 }}>{confidenceBadge(item.data_confidence)}</td>
      <td style={{ padding: '8px 6px', fontSize: 12, color: 'var(--text-secondary)' }}>
        {item.reasoning}
        <SubScoreExpander sub={item.sub_scores} />
      </td>
      <td style={{ padding: '8px 6px', width: 180 }}>
        <span style={{ display: 'inline-flex', gap: 4 }}>
          {(['accepted', 'rejected'] as const).map((opt) => {
            const label = opt === 'accepted' ? 'Accept' : 'Reject'
            const active = decision === opt
            return (
              <button
                key={label}
                onClick={() => onDecide(item._key!, active ? undefined : opt)}
                style={{
                  fontSize: 11,
                  padding: '3px 8px',
                  borderRadius: 4,
                  border: '1px solid var(--border)',
                  cursor: 'pointer',
                  fontWeight: active ? 700 : 400,
                  backgroundColor: active
                    ? opt === 'accepted'
                      ? '#16a34a'
                      : '#dc2626'
                    : 'var(--bg-secondary)',
                  color: active ? '#fff' : 'var(--text-secondary)',
                }}
              >
                {label}
              </button>
            )
          })}
        </span>
      </td>
    </tr>
  )
}

// ── Collapsible section ───────────────────────────────────────────────────────

function Collapsible({ label, count, defaultOpen = true, children }: {
  label: string
  count: number
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  if (count === 0) return null
  return (
    <div style={{ marginTop: 12 }}>
      <button
        onClick={() => setOpen((p) => !p)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12,
          fontWeight: 600,
          color: 'var(--text-secondary)',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: '2px 0',
        }}
      >
        <span style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)', display: 'inline-block', transition: 'transform 0.15s' }}>▼</span>
        {label} ({count})
      </button>
      {open && children}
    </div>
  )
}

// ── Watchlist section ─────────────────────────────────────────────────────────

function WatchlistSection({
  name,
  data,
  decisions,
  onDecide,
}: {
  name: string
  data: WatchlistRecs
  decisions: Map<string, Decision>
  onDecide: (key: string, d: Decision | undefined) => void
}) {
  const [open, setOpen] = useState(true)
  const DISPLAY_NAMES: Record<string, string> = {
    wheel: 'Wheel',
    iron_condor: 'Iron Condor',
    iron_butterfly: 'Iron Butterfly',
    spreads: 'Spreads',
    calendar_spread: 'Calendar Spread',
  }
  const displayName = DISPLAY_NAMES[name] ?? (name.charAt(0).toUpperCase() + name.slice(1))
  const totalRecs = data.add.length + data.remove.length

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 8,
        marginBottom: 16,
        backgroundColor: 'var(--bg-card)',
      }}
    >
      <button
        onClick={() => setOpen((p) => !p)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          width: '100%',
          padding: '12px 16px',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          borderRadius: 8,
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>{displayName}</span>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {data.current_members.length} current members
          </span>
          {totalRecs > 0 && (
            <span
              style={{
                fontSize: 11,
                padding: '2px 8px',
                borderRadius: 10,
                backgroundColor: 'color-mix(in srgb, var(--accent) 20%, var(--bg-card))',
                color: 'var(--accent)',
                fontWeight: 700,
              }}
            >
              {totalRecs} pending
            </span>
          )}
        </span>
        <span style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)', display: 'inline-block', transition: 'transform 0.15s', color: 'var(--text-muted)', fontSize: 12 }}>▼</span>
      </button>

      {open && (
        <div style={{ padding: '0 16px 16px' }}>
          {/* Additions */}
          {data.add.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#22c55e', marginBottom: 6 }}>
                Recommended additions
              </div>
              <RecTable items={data.add} decisions={decisions} onDecide={onDecide} actionLabel="ADD" />
            </div>
          )}

          {/* Removals */}
          {data.remove.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#ef4444', marginBottom: 6 }}>
                Recommended removals
              </div>
              <RecTable items={data.remove} decisions={decisions} onDecide={onDecide} actionLabel="REMOVE" />
            </div>
          )}

          {/* No change — collapsed */}
          <Collapsible label="Current members — no change" count={data.no_change.length} defaultOpen={false}>
            <RecTable items={data.no_change} decisions={decisions} onDecide={onDecide} actionLabel="HOLD" />
          </Collapsible>

          {/* Also considered — collapsed */}
          <Collapsible label="Also considered" count={data.considered_but_rejected.length} defaultOpen={false}>
            <RecTable items={data.considered_but_rejected} decisions={decisions} onDecide={onDecide} actionLabel="SKIP" />
          </Collapsible>
        </div>
      )}
    </div>
  )
}

// ── Rec table ─────────────────────────────────────────────────────────────────

function RecTable({
  items,
  decisions,
  onDecide,
  actionLabel,
}: {
  items: RecItem[]
  decisions: Map<string, Decision>
  onDecide: (key: string, d: Decision | undefined) => void
  actionLabel: string
}) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr style={{ borderBottom: '1px solid var(--border)' }}>
          {['Symbol', 'Score', 'Confidence', 'Reasoning', 'Decision'].map((h) => (
            <th
              key={h}
              style={{
                textAlign: 'left',
                padding: '4px 6px',
                fontSize: 11,
                color: 'var(--text-muted)',
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: '0.04em',
              }}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <RecRow
            key={item._key}
            item={item}
            decision={decisions.get(item._key!)}
            onDecide={onDecide}
            actionLabel={actionLabel}
          />
        ))}
      </tbody>
    </table>
  )
}

// ── Confirmation modal ────────────────────────────────────────────────────────

function ConfirmModal({
  decisions,
  allRecs,
  onConfirm,
  onCancel,
  loading,
}: {
  decisions: Map<string, Decision>
  allRecs: RecItem[]
  onConfirm: () => void
  onCancel: () => void
  loading: boolean
}) {
  const recByKey = new Map(allRecs.map((r) => [r._key!, r]))
  const accepted = [...decisions.entries()].filter(([, d]) => d === 'accepted').map(([key]) => recByKey.get(key)).filter(Boolean) as RecItem[]
  const rejected = [...decisions.entries()].filter(([, d]) => d === 'rejected').map(([key]) => recByKey.get(key)).filter(Boolean) as RecItem[]
  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        backgroundColor: 'rgba(0,0,0,0.55)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={onCancel}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 10,
          padding: 28,
          minWidth: 380,
          maxWidth: 540,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 style={{ margin: '0 0 12px', fontSize: 16, color: 'var(--text-primary)' }}>
          Confirm selections
        </h2>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16 }}>
          {accepted.length} accepted · {rejected.length} rejected
        </p>

        {accepted.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#22c55e', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Accepted
            </div>
            {accepted.map((r) => (
              <div key={r._key} style={{ fontSize: 13, color: 'var(--text-primary)', padding: '2px 0' }}>
                <strong>{r.symbol}</strong>{' '}
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{r.reasoning}</span>
              </div>
            ))}
          </div>
        )}

        {rejected.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#ef4444', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Rejected
            </div>
            {rejected.map((r) => (
              <div key={r._key} style={{ fontSize: 13, color: 'var(--text-muted)', padding: '2px 0' }}>
                {r.symbol}
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            disabled={loading}
            style={{
              padding: '7px 18px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'var(--bg-secondary)', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 13,
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            style={{
              padding: '7px 18px', borderRadius: 6, border: 'none',
              background: 'var(--accent)', color: '#fff', cursor: loading ? 'wait' : 'pointer', fontSize: 13, fontWeight: 600,
            }}
          >
            {loading ? 'Applying…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function Toast({ message, type }: { message: string; type: 'success' | 'error' }) {
  return (
    <div
      style={{
        position: 'fixed', bottom: 28, right: 28, zIndex: 2000,
        padding: '12px 20px', borderRadius: 8,
        backgroundColor: type === 'success' ? '#16a34a' : '#dc2626',
        color: '#fff', fontSize: 14, fontWeight: 600,
        boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
      }}
    >
      {message}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Recommendations() {
  const navigate = useNavigate()
  const [data, setData] = useState<RecsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [decisions, setDecisions] = useState<Map<string, Decision>>(new Map())
  const [showModal, setShowModal] = useState(false)
  const [applying, setApplying] = useState(false)
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null)
  const [lastRun, setLastRun] = useState<ResearchLastRunResponse | null>(null)

  useEffect(() => {
    api.researchLastRun().then(setLastRun).catch(() => {})
  }, [])

  useEffect(() => {
    fetch('/api/research/recommendations', { credentials: 'same-origin' })
      .then((r) => {
        if (r.status === 404) throw new Error('No recommendations generated yet. Run the weekly research job first.')
        if (!r.ok) throw new Error('Failed to load recommendations')
        return r.json()
      })
      .then((d: RecsResponse) => {
          // Stamp a stable unique key on every item. Use recommendation_id when
          // present (backend fix); fall back to a composite so the UI works even
          // when the snapshot pre-dates the backend fix.
          let idx = 0
          for (const wl of Object.values(d.watchlists)) {
            for (const group of [wl.add, wl.remove, wl.no_change, wl.considered_but_rejected]) {
              for (const item of group) {
                item._key = item.recommendation_id != null
                  ? String(item.recommendation_id)
                  : `_${idx++}`
              }
            }
          }
          setData(d)
          setLoading(false)
        })
      .catch((e) => { setError(e.message); setLoading(false) })
  }, [])

  const handleDecide = (key: string, d: Decision | undefined) => {
    setDecisions((prev) => {
      const next = new Map(prev)
      if (d === undefined) next.delete(key)
      else next.set(key, d)
      return next
    })
  }

  const hasDecisions = decisions.size > 0

  // Flatten all rec items for confirmation modal
  const allRecs: RecItem[] = data
    ? Object.values(data.watchlists).flatMap((wl) => [
        ...wl.add,
        ...wl.remove,
        ...wl.no_change,
        ...wl.considered_but_rejected,
      ])
    : []

  const handleApply = async () => {
    setApplying(true)
    const recByKey = new Map(allRecs.map((r) => [r._key!, r]))
    const accepted = [...decisions.entries()]
      .filter(([, d]) => d === 'accepted')
      .map(([key]) => recByKey.get(key))
      .filter((r): r is RecItem => r?.recommendation_id != null)
      .map((r) => ({ recommendation_id: r.recommendation_id }))
    const rejected = [...decisions.entries()]
      .filter(([, d]) => d === 'rejected')
      .map(([key]) => recByKey.get(key))
      .filter((r): r is RecItem => r?.recommendation_id != null)
      .map((r) => ({ recommendation_id: r.recommendation_id }))

    try {
      const r = await fetch('/api/research/recommendations/apply', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accepted, rejected }),
      })
      const body = await r.json()
      if (!r.ok) {
        const msg = body.error ?? body.errors?.join('; ') ?? 'Apply failed'
        setToast({ message: msg, type: 'error' })
        setShowModal(false)
        setApplying(false)
        setTimeout(() => setToast(null), 4000)
        return
      }
      setToast({ message: `Applied: ${body.applied} accepted, ${body.rejected} rejected`, type: 'success' })
      setShowModal(false)
      setApplying(false)
      setTimeout(() => {
        setToast(null)
        navigate('/research')
      }, 1800)
    } catch {
      setToast({ message: 'Network error during apply', type: 'error' })
      setShowModal(false)
      setApplying(false)
      setTimeout(() => setToast(null), 4000)
    }
  }

  if (loading) {
    return (
      <div className="p-5" style={{ color: 'var(--text-muted)' }}>
        Loading recommendations…
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="p-5">
        <h1 className="text-2xl font-bold mb-2" style={{ color: 'var(--text-primary)' }}>
          Watchlist Recommendations
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>{error ?? 'No data'}</p>
      </div>
    )
  }

  return (
    <div className="p-5 max-w-screen-xl mx-auto">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-2xl font-bold mb-1" style={{ color: 'var(--text-primary)' }}>
          Watchlist Recommendations
        </h1>
        <div className="flex items-center gap-4 flex-wrap">
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Approve or reject suggested watchlist changes
            {data.generated_at && (
              <span style={{ marginLeft: 8, opacity: 0.7 }}>
                · Generated {timeAgo(data.generated_at)}
              </span>
            )}
          </p>
          {lastRun && (
            <StalenessBadge last_updated={lastRun.most_recent_recommendation} label="recommendations" />
          )}
        </div>
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>
        {([
          { label: 'ADD', color: '#22c55e', desc: 'Not on watchlist — recommended to add' },
          { label: 'REMOVE', color: '#ef4444', desc: 'On watchlist — recommended to remove' },
          { label: 'HOLD', color: '#6b7280', desc: 'On watchlist — no change needed' },
          { label: 'SKIP', color: '#6b7280', desc: 'Considered but not recommended' },
        ] as const).map(({ label, color, desc }) => (
          <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-muted)' }}>
            <span style={{
              fontSize: 10, padding: '1px 5px', borderRadius: 4, fontWeight: 700,
              backgroundColor: `color-mix(in srgb, ${color} 20%, var(--bg-card))`,
              color,
            }}>{label}</span>
            {desc}
          </span>
        ))}
      </div>

      {/* Watchlist sections */}
      {(['wheel', 'iron_condor', 'iron_butterfly', 'spreads'] as const).map((key) => (
        <WatchlistSection
          key={key}
          name={key}
          data={data.watchlists[key]}
          decisions={decisions}
          onDecide={handleDecide}
        />
      ))}

      {/* Footer apply button */}
      <div
        style={{
          position: 'sticky', bottom: 0, left: 0, right: 0,
          padding: '14px 20px',
          backgroundColor: 'var(--bg-card)',
          borderTop: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 12,
        }}
      >
        {decisions.size > 0 && (
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {decisions.size} decision{decisions.size !== 1 ? 's' : ''} pending
          </span>
        )}
        <button
          disabled={!hasDecisions}
          onClick={() => setShowModal(true)}
          style={{
            padding: '8px 22px', borderRadius: 6, border: 'none',
            backgroundColor: hasDecisions ? 'var(--accent)' : 'var(--border)',
            color: hasDecisions ? '#fff' : 'var(--text-muted)',
            cursor: hasDecisions ? 'pointer' : 'not-allowed',
            fontWeight: 600, fontSize: 14,
            transition: 'background-color 0.15s',
          }}
        >
          Apply selections
        </button>
      </div>

      {/* Confirmation modal */}
      {showModal && (
        <ConfirmModal
          decisions={decisions}
          allRecs={allRecs}
          onConfirm={handleApply}
          onCancel={() => setShowModal(false)}
          loading={applying}
        />
      )}

      {/* Toast */}
      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  )
}
