import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { useAccounts } from '../hooks/useAccounts'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import type { Decision, DecisionReasoning } from '../types'

const PAGE_SIZE = 25

const ACTION_OPTIONS = [
  { value: '', label: 'All actions' },
  { value: 'SELL_PUT', label: 'sell_put' },
  { value: 'SELL_CALL', label: 'sell_call' },
  { value: 'OPEN', label: 'open' },
  { value: 'ROLL', label: 'roll' },
  { value: 'CLOSE', label: 'close' },
  { value: 'HOLD', label: 'hold' },
  { value: 'SKIP', label: 'skip' },
]

const CONFIDENCE_OPTIONS = [
  { value: '', label: 'All confidence', numeric: undefined },
  { value: 'HIGH', label: 'High', numeric: 0.9 },
  { value: 'MEDIUM', label: 'Medium', numeric: 0.6 },
  { value: 'LOW', label: 'Low', numeric: 0.3 },
] as const

const REASONING_KEYS: { key: keyof DecisionReasoning; label: string }[] = [
  { key: 'macro', label: 'Macro' },
  { key: 'fundamental', label: 'Fundamental' },
  { key: 'technical', label: 'Technical' },
  { key: 'volatility', label: 'Volatility' },
  { key: 'selection', label: 'Selection' },
  { key: 'risk', label: 'Risk' },
]

function isReasoningObject(r: Decision['reasoning']): r is DecisionReasoning {
  return r != null && typeof r === 'object'
}

function ReasoningRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3 py-1.5">
      <div
        className="text-[10px] uppercase tracking-wider w-24 shrink-0 pt-0.5"
        style={{ color: 'var(--text-muted)' }}
      >
        {label}
      </div>
      <div className="text-sm flex-1" style={{ color: 'var(--text-primary)' }}>
        {value}
      </div>
    </div>
  )
}

function DecisionCard({ d }: { d: Decision }) {
  const ts = d.timestamp || d.created_at || ''
  const tsDisplay = ts ? new Date(ts).toLocaleString() : '—'
  const r = d.reasoning
  const action = (d.action || '').toUpperCase()
  const showSkipReason =
    (action === 'SKIP' || action === 'HOLD') && d.skip_reason

  return (
    <article
      className="rounded-md p-5"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      {/* Header */}
      <header className="flex items-start justify-between gap-3 mb-3 pb-3"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <div className="flex items-center flex-wrap gap-2 text-xs">
          <Badge variant="action">{d.action}</Badge>
          <span className="font-mono text-sm" style={{ color: 'var(--text-primary)' }}>
            {d.underlying}
          </span>
          <span style={{ color: 'var(--text-muted)' }}>·</span>
          <Badge variant="neutral">{d.strategy_type}</Badge>
          <span style={{ color: 'var(--text-muted)' }}>·</span>
          <span className="font-mono tabular text-xs" style={{ color: 'var(--text-muted)' }}>
            {tsDisplay}
          </span>
        </div>
        {d.confidence != null && (
          <Badge variant="confidence" value={d.confidence} />
        )}
      </header>

      {/* Body — reasoning (always visible) */}
      <div>
        {r == null ? (
          <div className="italic text-sm" style={{ color: 'var(--text-muted)' }}>
            Rejected by guardrails before Claude evaluated this decision.
          </div>
        ) : isReasoningObject(r) ? (
          REASONING_KEYS
            .map(({ key, label }) => ({ key, label, value: r[key] }))
            .filter((row): row is { key: keyof DecisionReasoning; label: string; value: string } =>
              typeof row.value === 'string' && row.value.trim().length > 0,
            )
            .map(({ key, label, value }) => (
              <ReasoningRow key={key} label={label} value={value} />
            ))
        ) : (
          // String reasoning (e.g. guardrail rejection text or recorder
          // string-encoded dict that we can't safely re-parse here).
          <div className="text-sm whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>
            {r}
          </div>
        )}
      </div>

      {showSkipReason && (
        <footer
          className="mt-3 pt-3 flex items-start gap-2 text-sm"
          style={{
            borderTop: '1px solid var(--border)',
            color: 'var(--yellow)',
          }}
        >
          <span aria-hidden>⚠</span>
          <span>
            <span className="text-[11px] uppercase tracking-wider mr-2 opacity-75">
              Skip reason:
            </span>
            {d.skip_reason}
          </span>
        </footer>
      )}
    </article>
  )
}

function SkeletonCard() {
  return (
    <div
      className="rounded-md p-5 animate-pulse"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      <div className="flex items-center gap-2 mb-4">
        <div className="h-4 w-16 rounded" style={{ backgroundColor: 'var(--border)' }} />
        <div className="h-4 w-20 rounded" style={{ backgroundColor: 'var(--border)' }} />
        <div className="h-4 w-32 rounded ml-auto" style={{ backgroundColor: 'var(--border)' }} />
      </div>
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="flex gap-3 py-1.5">
          <div className="h-3 w-20 rounded" style={{ backgroundColor: 'var(--border)' }} />
          <div className="h-3 flex-1 rounded" style={{ backgroundColor: 'var(--border)' }} />
        </div>
      ))}
    </div>
  )
}

export function ReasoningExplorer() {
  const [account, setAccount] = useState<string>('')
  const [actionFilter, setActionFilter] = useState<string>('')
  const { accounts } = useAccounts()
  const [confidenceLabel, setConfidenceLabel] = useState<string>('')
  const [underlyingInput, setUnderlyingInput] = useState<string>('')
  const [debouncedUnderlying, setDebouncedUnderlying] = useState<string>('')

  const [decisions, setDecisions] = useState<Decision[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const initialFetchRef = useRef(true)

  // Debounce the underlying text input so we don't fire a request per keystroke.
  useEffect(() => {
    const t = window.setTimeout(() => {
      setDebouncedUnderlying(underlyingInput.trim().toUpperCase())
    }, 300)
    return () => window.clearTimeout(t)
  }, [underlyingInput])

  const confidenceNumeric = useMemo(() => {
    return CONFIDENCE_OPTIONS.find((o) => o.value === confidenceLabel)?.numeric
  }, [confidenceLabel])

  const baseParams = useMemo(() => ({
    account: account || undefined,
    action: actionFilter || undefined,
    underlying: debouncedUnderlying || undefined,
    confidence: confidenceNumeric,
  }), [account, actionFilter, debouncedUnderlying, confidenceNumeric])

  // Refetch from offset 0 whenever filters change.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setOffset(0)
    api
      .decisions({ ...baseParams, limit: PAGE_SIZE, offset: 0 })
      .then((r) => {
        if (cancelled) return
        setDecisions(r.decisions)
        setTotal(r.total)
        initialFetchRef.current = false
      })
      .catch((e) => {
        if (!cancelled) setError(String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [baseParams, reloadKey])

  const loadMore = async () => {
    if (loadingMore || !decisions) return
    const nextOffset = decisions.length
    setLoadingMore(true)
    try {
      const r = await api.decisions({
        ...baseParams,
        limit: PAGE_SIZE,
        offset: nextOffset,
      })
      setDecisions((prev) => [...(prev ?? []), ...r.decisions])
      setOffset(nextOffset)
      setTotal(r.total)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoadingMore(false)
    }
  }

  const hasMore = decisions != null && decisions.length < total
  const showSkeleton = loading && initialFetchRef.current

  return (
    <div className="space-y-4 max-w-4xl">
      <h2 className="text-2xl font-semibold">Reasoning</h2>

      {/* Filters */}
      <div
        className="rounded p-3 flex flex-wrap items-center gap-3"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <label className="flex items-center gap-2 text-xs">
          <span style={{ color: 'var(--text-muted)' }}>Account</span>
          <select
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            className="text-xs px-2 py-1 rounded"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          >
            <option value="">All</option>
            {accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>{a.label}</option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-xs">
          <span style={{ color: 'var(--text-muted)' }}>Action</span>
          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="text-xs px-2 py-1 rounded"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          >
            {ACTION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-xs">
          <span style={{ color: 'var(--text-muted)' }}>Confidence</span>
          <select
            value={confidenceLabel}
            onChange={(e) => setConfidenceLabel(e.target.value)}
            className="text-xs px-2 py-1 rounded"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          >
            {CONFIDENCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-xs">
          <span style={{ color: 'var(--text-muted)' }}>Underlying</span>
          <input
            value={underlyingInput}
            onChange={(e) => setUnderlyingInput(e.target.value)}
            placeholder="AAPL"
            className="text-xs px-2 py-1 rounded font-mono w-24"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          />
        </label>
      </div>

      {/* Total count */}
      {!showSkeleton && decisions != null && (
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {total === 0
            ? 'No decisions match these filters.'
            : `Showing ${decisions.length} of ${total} decision${total === 1 ? '' : 's'}.`}
        </div>
      )}

      {error && (
        <div
          className="text-xs p-3 rounded flex items-center justify-between gap-3"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--red) 12%, transparent)',
            color: 'var(--red)',
            border: '1px solid color-mix(in srgb, var(--red) 35%, transparent)',
          }}
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setReloadKey((k) => k + 1)}
            className="px-2 py-1 rounded text-xs font-medium"
            style={{
              backgroundColor: 'color-mix(in srgb, var(--red) 20%, transparent)',
              color: 'var(--red)',
              border: '1px solid color-mix(in srgb, var(--red) 45%, transparent)',
            }}
          >
            Retry
          </button>
        </div>
      )}

      {/* Feed */}
      <div className="space-y-3">
        {showSkeleton ? (
          <>
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </>
        ) : decisions != null && decisions.length === 0 ? (
          <EmptyState
            message="No decisions recorded yet."
            hint="The bot will populate this feed when it starts trading."
          />
        ) : (
          decisions?.map((d) => <DecisionCard key={d.id} d={d} />)
        )}
      </div>

      {/* Load more */}
      {!showSkeleton && hasMore && (
        <div className="flex justify-center pt-2">
          <button
            onClick={loadMore}
            disabled={loadingMore}
            className="text-xs px-4 py-2 rounded font-semibold tracking-wider uppercase"
            style={{
              backgroundColor: loadingMore ? 'var(--bg-card)' : 'var(--accent)',
              color: loadingMore ? 'var(--text-muted)' : 'var(--bg-primary)',
              border: '1px solid var(--accent)',
              cursor: loadingMore ? 'wait' : 'pointer',
              opacity: loadingMore ? 0.6 : 1,
            }}
          >
            {loadingMore ? 'Loading…' : `Load more (${total - (decisions?.length ?? 0)} remaining)`}
          </button>
        </div>
      )}

      {/* Suppress unused-state lint: offset is intentionally tracked but
          rendered indirectly through `decisions.length`. */}
      <span className="hidden">{offset}</span>
    </div>
  )
}
