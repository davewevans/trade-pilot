import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type SpotCheckDecision,
  type SpotCheckQueueItem,
} from '../../api/client'
import { useCanMutate } from '../../context/AuthContext'

// ── Helpers ───────────────────────────────────────────────────────────────────

function scoreColor(score: number): string {
  if (score >= 0.7) return 'var(--green)'
  if (score >= 0.5) return 'var(--yellow)'
  return 'var(--red)'
}

function dimLabel(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function toStr(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return JSON.stringify(v)
}

// Extract labelled rows for the left context pane.
// Priority order matches the spec: symbol, strategy, regime, IV env, action, contract.
function buildContextRows(item: SpotCheckQueueItem): Array<[string, string]> {
  const d = item.decision
  const ctx = (typeof d.context === 'object' && d.context !== null
    ? d.context
    : {}) as Record<string, unknown>

  const rows: Array<[string, string]> = []
  const push = (label: string, val: unknown) => {
    const s = toStr(val)
    if (s) rows.push([label, s])
  }

  push('Symbol', d.underlying)
  push('Strategy', d['strategy_type'] ?? ctx['strategy_type'] ?? ctx['strategy'])
  push('Action', d.action)
  push('Regime', ctx['regime'] ?? ctx['market_regime'] ?? ctx['confirmed_market_regime'])
  push(
    'IV Environment',
    ctx['iv_environment'] ?? ctx['iv_regime'] ?? ctx['iv_rank_bucket'] ?? ctx['iv_env'],
  )

  // Selected contract — extract symbol or strike if it's an object
  const rawContract =
    ctx['selected_contract'] ?? ctx['selected_option'] ?? ctx['contract']
  if (rawContract != null) {
    const cv =
      typeof rawContract === 'object' && rawContract !== null
        ? (rawContract as Record<string, unknown>)['symbol'] ??
          (rawContract as Record<string, unknown>)['strike'] ??
          JSON.stringify(rawContract)
        : rawContract
    push('Contract', cv)
  }

  return rows
}

// Extract up to 6 key-value pairs from the reasoning object for display.
function buildReasoningRows(d: SpotCheckDecision): Array<[string, string]> {
  const r = d.reasoning
  if (typeof r !== 'object' || r === null) return []
  return Object.entries(r)
    .filter(([, v]) => v != null && v !== '')
    .slice(0, 6)
    .map(([k, v]) => [dimLabel(k), toStr(v).slice(0, 350)])
}

// ── Note modal ────────────────────────────────────────────────────────────────

interface NoteModalProps {
  initial: string
  onCommit: (note: string) => void
  onClose: () => void
}

function NoteModal({ initial, onCommit, onClose }: NoteModalProps) {
  const [text, setText] = useState(initial)
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    ref.current?.focus()
  }, [])

  const commit = () => {
    onCommit(text.trim())
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)' }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="rounded-lg p-5 w-full max-w-md mx-4 space-y-3"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
          Add note
        </div>
        <textarea
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              commit()
            }
            if (e.key === 'Escape') onClose()
          }}
          placeholder="Note for this verdict…"
          rows={3}
          className="w-full rounded px-3 py-2 text-sm resize-none"
          style={{
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            outline: 'none',
          }}
        />
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
          Enter to save · Esc to cancel · Shift+Enter for newline
        </div>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded text-sm"
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
            onClick={commit}
            className="px-3 py-1.5 rounded text-sm font-medium"
            style={{ backgroundColor: 'var(--accent)', color: '#fff', border: 'none', cursor: 'pointer' }}
          >
            Save note
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Verdict button row ─────────────────────────────────────────────────────────

const VERDICT_BUTTONS = [
  { key: 'A', label: 'Agree', verdict: 'agree', color: 'var(--green)' },
  { key: 'D', label: 'Disagree', verdict: 'disagree', color: 'var(--red)' },
  { key: 'U', label: 'Unclear', verdict: 'unclear', color: 'var(--yellow)' },
] as const

// ── Main component ────────────────────────────────────────────────────────────

interface SpotCheckQueueProps {
  month: string
}

export function SpotCheckQueue({ month }: SpotCheckQueueProps) {
  const canMutate = useCanMutate()
  const [expanded, setExpanded] = useState(false)
  const [queue, setQueue] = useState<SpotCheckQueueItem[]>([])
  // Track submitted score IDs for optimistic updates and rollback.
  const [submitted, setSubmitted] = useState<Set<number>>(new Set())
  // Index within the *remaining* (unsubmitted) slice of the queue.
  const [index, setIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [noteModalOpen, setNoteModalOpen] = useState(false)
  // Note to attach to the next verdict submission.
  const [pendingNote, setPendingNote] = useState<string | null>(null)

  // Fixed at first load for the progress bar denominator.
  const originalTotal = useRef(0)

  // Load on mount so the count is available for the collapsed label.
  useEffect(() => {
    setLoading(true)
    api
      .getSpotCheckQueue(month, 100)
      .then((r) => {
        setQueue(r.queue)
        originalTotal.current = r.queue.length
      })
      .catch((e) => setFetchError(String((e as Error)?.message ?? e)))
      .finally(() => setLoading(false))
  }, [month])

  // Derived queue state — items not yet submitted (optimistic or confirmed).
  const remaining = queue.filter((item) => !submitted.has(item.score.id))
  const currentIndex = Math.min(index, Math.max(0, remaining.length - 1))
  const current = remaining.length > 0 ? remaining[currentIndex] : null

  const submitVerdict = useCallback(
    async (verdict: 'agree' | 'disagree' | 'unclear') => {
      if (!current) return
      const note = pendingNote ?? undefined
      const scoreId = current.score.id

      // Optimistic: mark submitted immediately so the next item appears.
      setSubmitted((prev) => new Set([...prev, scoreId]))
      setPendingNote(null)
      setSubmitError(null)

      try {
        await api.submitSpotCheck(month, scoreId, verdict, note)
      } catch (e) {
        // Rollback: remove from submitted set so the item reappears.
        setSubmitted((prev) => {
          const next = new Set(prev)
          next.delete(scoreId)
          return next
        })
        setSubmitError(String((e as Error)?.message ?? e))
      }
    },
    [current, month, pendingNote],
  )

  // Keyboard handler stored in a ref so the effect doesn't need to re-register
  // every render — the ref always holds the latest closure.
  const handlerRef = useRef<(e: KeyboardEvent) => void>(() => {})
  handlerRef.current = (e: KeyboardEvent) => {
    // Don't steal events while the note modal is open.
    if (noteModalOpen) return
    // Don't steal events while focus is in a text input.
    const tag = (e.target as HTMLElement)?.tagName?.toLowerCase()
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return
    if (!current) return

    switch (e.key.toLowerCase()) {
      case 'a':
        e.preventDefault()
        void submitVerdict('agree')
        break
      case 'd':
        e.preventDefault()
        void submitVerdict('disagree')
        break
      case 'u':
        e.preventDefault()
        void submitVerdict('unclear')
        break
      case 'n':
        e.preventDefault()
        setNoteModalOpen(true)
        break
      case 'arrowright':
        e.preventDefault()
        setIndex((i) => Math.min(i + 1, remaining.length - 1))
        break
      case 'arrowleft':
        e.preventDefault()
        setIndex((i) => Math.max(i - 1, 0))
        break
    }
  }

  // Register the listener only while expanded; clean up on collapse or unmount.
  useEffect(() => {
    if (!expanded) return
    const handler = (e: KeyboardEvent) => handlerRef.current(e)
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [expanded])

  // ── Collapsed state ──────────────────────────────────────────────────────────

  if (!expanded) {
    if (loading) {
      return (
        <div
          className="rounded-md px-4 py-3 text-sm"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
        >
          Loading spot-check queue…
        </div>
      )
    }
    if (fetchError) {
      return (
        <div
          className="rounded-md px-4 py-3 text-sm"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--red)' }}
        >
          {fetchError}
        </div>
      )
    }
    if (queue.length === 0) {
      return (
        <div
          className="rounded-md px-4 py-6 text-center text-sm"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
        >
          Spot-check queue complete for this month. Good work.
        </div>
      )
    }
    return (
      <div
        className="rounded-md overflow-hidden"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <button
          onClick={() => setExpanded(true)}
          className="w-full text-left px-4 py-3 flex items-center justify-between gap-3"
          style={{ background: 'none', border: 'none', cursor: 'pointer' }}
        >
          <div className="flex items-center gap-2">
            <span
              className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--accent) 15%, transparent)',
                color: 'var(--accent)',
                border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
              }}
            >
              {remaining.length}
            </span>
            <span className="text-sm" style={{ color: 'var(--text-primary)' }}>
              {remaining.length === 1 ? '1 item' : `${remaining.length} items`} pending — click to review
            </span>
          </div>
          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>▼</span>
        </button>
      </div>
    )
  }

  // ── Expanded: empty queue (all submitted this session) ────────────────────────

  if (remaining.length === 0) {
    return (
      <div
        className="rounded-md px-4 py-8 text-center"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <div className="text-sm font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Spot-check queue complete for this month. Good work.
        </div>
        <div className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
          {originalTotal.current} decision{originalTotal.current !== 1 ? 's' : ''} reviewed
        </div>
        <button
          onClick={() => setExpanded(false)}
          className="text-xs"
          style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}
        >
          Collapse
        </button>
      </div>
    )
  }

  // ── Expanded: active review UI ────────────────────────────────────────────────

  const contextRows = buildContextRows(current!)
  const reasoningRows = buildReasoningRows(current!.decision)
  const dimScores = current!.score.dimension_scores ?? []
  const totalScore = current!.score.total_score
  // Progress position counts already-submitted items plus current position in remaining.
  const position = submitted.size + currentIndex + 1
  const progressPct =
    originalTotal.current > 0
      ? Math.round((position / originalTotal.current) * 100)
      : 0

  return (
    <>
      {noteModalOpen && (
        <NoteModal
          initial={pendingNote ?? ''}
          onCommit={(text) => setPendingNote(text || null)}
          onClose={() => setNoteModalOpen(false)}
        />
      )}

      <div
        className="rounded-lg overflow-hidden"
        style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-card)' }}
      >
        {/* ── Progress header ── */}
        <div
          className="px-4 py-2 flex items-center gap-3"
          style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}
        >
          <span className="text-xs font-medium shrink-0" style={{ color: 'var(--text-secondary)' }}>
            Decision {position} of {originalTotal.current}
          </span>
          <div
            className="flex-1 h-1.5 rounded-full overflow-hidden"
            style={{ backgroundColor: 'var(--border)' }}
          >
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{ width: `${progressPct}%`, backgroundColor: 'var(--accent)' }}
            />
          </div>
          <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)' }}>
            {progressPct}%
          </span>
          <button
            onClick={() => setExpanded(false)}
            className="text-xs ml-1 shrink-0"
            style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
            title="Collapse"
          >
            ✕
          </button>
        </div>

        {/* ── Split pane ── */}
        <div className="flex" style={{ minHeight: 260 }}>
          {/* Left: decision context */}
          <div
            className="flex-1 p-4 space-y-4 min-w-0 overflow-y-auto"
            style={{ borderRight: '1px solid var(--border)', maxHeight: 380 }}
          >
            <div
              className="text-[10px] uppercase tracking-wider font-semibold"
              style={{ color: 'var(--text-muted)' }}
            >
              Decision Context
            </div>

            {/* Metadata rows */}
            {contextRows.length > 0 && (
              <div className="space-y-1.5">
                {contextRows.map(([label, value]) => (
                  <div key={label} className="flex gap-2 text-xs">
                    <span
                      className="shrink-0 w-24 font-medium"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      {label}
                    </span>
                    <span
                      className="font-mono break-all"
                      style={{ color: 'var(--text-primary)' }}
                    >
                      {value}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Reasoning fields */}
            {reasoningRows.length > 0 && (
              <div className="space-y-2.5">
                <div
                  className="text-[10px] uppercase tracking-wider font-semibold"
                  style={{ color: 'var(--text-muted)' }}
                >
                  Reasoning
                </div>
                {reasoningRows.map(([label, value]) => (
                  <div key={label}>
                    <div
                      className="text-[10px] uppercase font-semibold mb-0.5"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      {label}
                    </div>
                    <div
                      className="text-xs"
                      style={{ color: 'var(--text-secondary)', lineHeight: 1.5 }}
                    >
                      {value}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {contextRows.length === 0 && reasoningRows.length === 0 && (
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                No context data available for this decision.
              </div>
            )}
          </div>

          {/* Right: judge scores */}
          <div
            className="shrink-0 p-4 space-y-2 overflow-y-auto"
            style={{ width: 260, maxHeight: 380 }}
          >
            <div
              className="text-[10px] uppercase tracking-wider font-semibold"
              style={{ color: 'var(--text-muted)' }}
            >
              Judge Scores
            </div>

            {dimScores.length === 0 ? (
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                No dimension scores available.
              </div>
            ) : (
              dimScores.map((ds) => {
                const color = scoreColor(ds.score)
                return (
                  <div key={ds.dimension} className="space-y-0.5">
                    <div className="flex items-center justify-between gap-1">
                      <span
                        className="text-xs truncate"
                        style={{ color: 'var(--text-secondary)' }}
                      >
                        {dimLabel(ds.dimension)}
                      </span>
                      <span
                        className="text-xs font-mono font-semibold shrink-0"
                        style={{ color }}
                      >
                        {ds.score.toFixed(2)}
                      </span>
                    </div>
                    {ds.justification && (
                      <div
                        className="text-[10px] rounded px-1.5 py-1"
                        style={{
                          color: 'var(--text-muted)',
                          backgroundColor: `color-mix(in srgb, ${color} 8%, var(--bg-secondary))`,
                          fontStyle: 'italic',
                          lineHeight: 1.35,
                        }}
                      >
                        {ds.justification.slice(0, 160)}
                      </div>
                    )}
                  </div>
                )
              })
            )}

            {totalScore != null && (
              <div
                className="pt-2 mt-2 flex items-center justify-between"
                style={{ borderTop: '1px solid var(--border)' }}
              >
                <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                  Total
                </span>
                <span
                  className="text-sm font-mono font-bold"
                  style={{ color: scoreColor(totalScore) }}
                >
                  {totalScore.toFixed(2)}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* ── Actions row ── */}
        <div
          className="px-4 py-3 space-y-2"
          style={{ borderTop: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}
        >
          {/* Submit error — shows and prompts retry */}
          {submitError && (
            <div
              className="text-xs px-2 py-1 rounded"
              style={{
                color: 'var(--red)',
                backgroundColor: 'color-mix(in srgb, var(--red) 10%, transparent)',
                border: '1px solid color-mix(in srgb, var(--red) 25%, transparent)',
              }}
            >
              {submitError} — verdict rolled back, please try again
            </div>
          )}

          {/* Pending note indicator */}
          {pendingNote && (
            <div
              className="text-xs px-2 py-1 rounded flex items-center justify-between gap-2"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--accent) 10%, transparent)',
                color: 'var(--text-secondary)',
                border: '1px solid color-mix(in srgb, var(--accent) 25%, transparent)',
              }}
            >
              <span className="truncate">Note: {pendingNote}</span>
              <button
                onClick={() => setPendingNote(null)}
                style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, flexShrink: 0 }}
                title="Clear note"
              >
                ✕
              </button>
            </div>
          )}

          {/* Verdict + navigation buttons */}
          <div className="flex items-center gap-2 flex-wrap">
            {canMutate && VERDICT_BUTTONS.map(({ key, label, verdict, color }) => (
              <button
                key={verdict}
                onClick={() => void submitVerdict(verdict)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium"
                style={{
                  backgroundColor: `color-mix(in srgb, ${color} 12%, var(--bg-card))`,
                  color,
                  border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
                  cursor: 'pointer',
                }}
              >
                <kbd
                  className="text-[10px] px-1 rounded font-mono"
                  style={{ backgroundColor: `color-mix(in srgb, ${color} 18%, transparent)` }}
                >
                  {key}
                </kbd>
                {label}
              </button>
            ))}

            <button
              onClick={() => setNoteModalOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm"
              style={{
                backgroundColor: 'var(--bg-card)',
                color: pendingNote ? 'var(--accent)' : 'var(--text-secondary)',
                border: `1px solid ${pendingNote ? 'color-mix(in srgb, var(--accent) 35%, transparent)' : 'var(--border)'}`,
                cursor: 'pointer',
              }}
            >
              <kbd
                className="text-[10px] px-1 rounded font-mono"
                style={{ backgroundColor: 'var(--bg-secondary)' }}
              >
                N
              </kbd>
              Note
            </button>

            {/* Prev / Next navigation */}
            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={() => setIndex((i) => Math.max(i - 1, 0))}
                disabled={currentIndex === 0}
                className="px-2.5 py-1.5 rounded text-sm"
                style={{
                  backgroundColor: 'var(--bg-card)',
                  color: currentIndex === 0 ? 'var(--text-muted)' : 'var(--text-secondary)',
                  border: '1px solid var(--border)',
                  cursor: currentIndex === 0 ? 'default' : 'pointer',
                  opacity: currentIndex === 0 ? 0.45 : 1,
                }}
                title="Previous (←)"
              >
                ←
              </button>
              <button
                onClick={() => setIndex((i) => Math.min(i + 1, remaining.length - 1))}
                disabled={currentIndex >= remaining.length - 1}
                className="px-2.5 py-1.5 rounded text-sm"
                style={{
                  backgroundColor: 'var(--bg-card)',
                  color:
                    currentIndex >= remaining.length - 1
                      ? 'var(--text-muted)'
                      : 'var(--text-secondary)',
                  border: '1px solid var(--border)',
                  cursor: currentIndex >= remaining.length - 1 ? 'default' : 'pointer',
                  opacity: currentIndex >= remaining.length - 1 ? 0.45 : 1,
                }}
                title="Next (→)"
              >
                →
              </button>
            </div>
          </div>

          {/* Keybinding legend */}
          <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            Keyboard: <kbd className="font-mono">A</kbd> agree ·{' '}
            <kbd className="font-mono">D</kbd> disagree ·{' '}
            <kbd className="font-mono">U</kbd> unclear ·{' '}
            <kbd className="font-mono">N</kbd> note ·{' '}
            <kbd className="font-mono">←</kbd> <kbd className="font-mono">→</kbd> navigate
          </div>
        </div>
      </div>
    </>
  )
}
