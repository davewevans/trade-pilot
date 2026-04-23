import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import type { CircuitBreaker, HaltInfo, HealthStatus } from '../../types'
import { Badge } from '../shared/Badge'

async function resetCircuitBreaker() {
  if (
    !window.confirm(
      'This will reset the circuit breaker and allow the bot to resume trading. Continue?',
    )
  ) {
    return
  }
  try {
    await api.resetCircuitBreaker()
    window.location.reload()
  } catch (err) {
    window.alert(
      `Reset failed: ${err instanceof Error ? err.message : String(err)}`,
    )
  }
}

function formatHaltedAt(halted_at: string | null | undefined): string {
  if (!halted_at) return ''
  try {
    return new Date(halted_at).toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    })
  } catch {
    return halted_at
  }
}

function sourceLabel(source: string | undefined): string {
  switch (source) {
    case 'dashboard': return 'via dashboard'
    case 'circuit_breaker': return 'by circuit breaker'
    case 'startup_reconcile': return 'by startup reconciler'
    case 'manual': return 'manually'
    default: return source ? `(${source})` : ''
  }
}

// ── Halt confirmation modal ──────────────────────────────────────────────────

function HaltModal({ onClose, onHalted }: { onClose: () => void; onHalted: (info: HaltInfo) => void }) {
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => { textareaRef.current?.focus() }, [])

  const submit = async () => {
    setLoading(true)
    setError('')
    try {
      const info = await api.halt(reason.trim())
      onHalted(info)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="rounded-lg p-6 max-w-md w-full space-y-4 mx-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <h2 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
          Halt trading?
        </h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          This will write <code>HALTED.lock</code> and stop all new entries.
          Existing positions will still be managed. Halt takes effect on the
          next cycle (within ~30 seconds).
        </p>
        <div className="space-y-1">
          <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
            Reason <span style={{ color: 'var(--text-muted)' }}>(optional)</span>
          </label>
          <textarea
            ref={textareaRef}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            className="w-full rounded px-3 py-2 text-sm resize-none"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
              outline: 'none',
            }}
            placeholder="e.g. SPY order looked wrong"
          />
        </div>
        {error && (
          <p className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>
        )}
        <div className="flex gap-3 justify-end">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="px-4 py-2 rounded text-sm"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border)',
              cursor: loading ? 'default' : 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={loading}
            className="px-4 py-2 rounded text-sm font-semibold"
            style={{
              backgroundColor: loading ? 'var(--text-muted)' : 'var(--red)',
              color: '#fff',
              cursor: loading ? 'default' : 'pointer',
              minHeight: '44px',
            }}
          >
            {loading ? 'Halting…' : 'Halt'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Resume confirmation modal ────────────────────────────────────────────────

function ResumeModal({ haltInfo, onClose, onResumed }: {
  haltInfo: HaltInfo
  onClose: () => void
  onResumed: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setLoading(true)
    setError('')
    try {
      await api.resume()
      onResumed()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  const haltedAtStr = formatHaltedAt(haltInfo.halted_at)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="rounded-lg p-6 max-w-md w-full space-y-4 mx-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <h2 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
          Resume trading?
        </h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          This will remove <code>HALTED.lock</code>. The bot will resume normal
          operation on its next cycle.
        </p>
        {(haltedAtStr || haltInfo.reason) && (
          <div
            className="rounded p-3 text-sm space-y-1"
            style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
          >
            {haltedAtStr && (
              <p style={{ color: 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--text-muted)' }}>Halted since: </span>
                {haltedAtStr}
                {haltInfo.source && ` ${sourceLabel(haltInfo.source)}`}
              </p>
            )}
            {haltInfo.reason && (
              <p style={{ color: 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--text-muted)' }}>Reason: </span>
                {haltInfo.reason}
              </p>
            )}
          </div>
        )}
        {error && (
          <p className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>
        )}
        <div className="flex gap-3 justify-end">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="px-4 py-2 rounded text-sm"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border)',
              cursor: loading ? 'default' : 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={loading}
            className="px-4 py-2 rounded text-sm font-semibold"
            style={{
              backgroundColor: loading ? 'var(--text-muted)' : 'var(--green)',
              color: '#fff',
              cursor: loading ? 'default' : 'pointer',
              minHeight: '44px',
            }}
          >
            {loading ? 'Resuming…' : 'Resume'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── TopBar ───────────────────────────────────────────────────────────────────

export function TopBar() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [cb, setCb] = useState<CircuitBreaker | null>(null)
  const [haltInfo, setHaltInfo] = useState<HaltInfo | null>(null)
  const [showHaltModal, setShowHaltModal] = useState(false)
  const [showResumeModal, setShowResumeModal] = useState(false)
  const [bundleDate, setBundleDate] = useState(() => {
    const et = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }))
    return et.toISOString().slice(0, 10)
  })

  useEffect(() => {
    let cancelled = false
    async function load() {
      const [h, c, hi] = await Promise.all([
        api.health().catch(() => null),
        api.circuitBreakers().catch(() => null),
        api.haltStatus().catch(() => null),
      ])
      if (!cancelled) {
        setHealth(h)
        setCb(c)
        setHaltInfo(hi)
      }
    }
    load()
    const id = setInterval(load, 30_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  const dryRun = Boolean(cb?.dry_run)
  const effectiveHalted = Boolean(haltInfo?.halted ?? health?.halted) && !dryRun

  const dotColor = !health
    ? 'var(--text-muted)'
    : effectiveHalted
      ? 'var(--red)'
      : 'var(--green)'

  const dotTitle = !health
    ? 'API unreachable'
    : effectiveHalted
      ? 'Halted'
      : dryRun
        ? 'Healthy (dry run)'
        : 'Healthy'

  const haltedAtStr = formatHaltedAt(haltInfo?.halted_at)

  return (
    <>
      <header
        className="flex items-center justify-between px-6 h-14 border-b"
        style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
      >
        <div className="flex items-center gap-3">
          <span
            className="inline-block w-2.5 h-2.5 rounded-full"
            style={{ backgroundColor: dotColor }}
            title={dotTitle}
          />
          <Link to="/" className="flex items-center gap-3 hover:opacity-80 transition-opacity" style={{ color: 'var(--text-primary)' }}>
            <img
              src="/favicon.svg"
              alt="trade-pilot"
              width={36}
              height={36}
            />
            <span className="font-semibold tracking-wide">trade-pilot</span>
          </Link>
          {effectiveHalted && <Badge variant="circuit" value="RED">HALTED</Badge>}
        </div>
        <div className="flex items-center gap-4 text-xs" style={{ color: 'var(--text-secondary)' }}>
          {cb && (
            <div className="flex items-center gap-2">
              <span className="uppercase tracking-wider opacity-75">Circuit:</span>
              <Badge variant="circuit" value={cb.status}>{cb.status}</Badge>
              {dryRun && (
                <span className="opacity-75" title="Circuit breaker is bypassed in DRY_RUN mode">
                  (dry run)
                </span>
              )}
              {!dryRun && (effectiveHalted || cb.status === 'RED') && (
                <button
                  type="button"
                  onClick={resetCircuitBreaker}
                  className="text-xs underline underline-offset-2 hover:opacity-80"
                  style={{ color: 'var(--red)' }}
                  title="Reset circuit breaker"
                >
                  Reset
                </button>
              )}
            </div>
          )}
          <div className="flex items-center gap-1">
            <input
              type="date"
              value={bundleDate}
              onChange={(e) => setBundleDate(e.target.value)}
              className="px-2 py-1 rounded text-xs border"
              style={{
                backgroundColor: 'transparent',
                borderColor: 'var(--border)',
                color: 'var(--text-secondary)',
                minHeight: '28px',
              }}
            />
            <button
              type="button"
              onClick={async () => {
                try {
                  const resp = await fetch(`/api/daily-evaluation-bundle?date=${bundleDate}`, { credentials: 'include' })
                  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
                  const blob = await resp.blob()
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = `trade-pilot-${bundleDate}.md`
                  document.body.appendChild(a)
                  a.click()
                  a.remove()
                  URL.revokeObjectURL(url)
                } catch (e) {
                  alert(`Bundle download failed: ${e}`)
                }
              }}
              className="px-3 py-1 rounded text-xs font-medium border hover:opacity-80 transition-opacity"
              style={{
                color: 'var(--text-secondary)',
                borderColor: 'var(--border)',
                backgroundColor: 'transparent',
                minHeight: '28px',
              }}
              title="Download daily evaluation bundle"
            >
              Daily Bundle
            </button>
          </div>
          {!dryRun && !effectiveHalted && (
            <button
              type="button"
              onClick={() => setShowHaltModal(true)}
              className="px-3 py-1 rounded text-xs font-medium border hover:opacity-80 transition-opacity"
              style={{
                color: 'var(--red)',
                borderColor: 'var(--red)',
                backgroundColor: 'transparent',
                minHeight: '28px',
              }}
              title="Halt the bot"
            >
              Halt
            </button>
          )}
          {health?.timestamp && (
            <span className="font-mono tabular">
              {new Date(health.timestamp).toLocaleDateString(undefined, {
                month: 'short', day: 'numeric', year: 'numeric',
              })}
              {' · '}
              {new Date(health.timestamp).toLocaleTimeString(undefined, {
                hour: 'numeric', minute: '2-digit',
              })}
            </span>
          )}
          {health?.version && (
            <a
              href="/changelog"
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono opacity-75 hover:opacity-100 underline-offset-2 hover:underline"
              title="View changelog"
            >
              trade-pilot v{health.version}
              {health.version_date ? ` · ${health.version_date}` : ''}
            </a>
          )}
        </div>
      </header>

      {effectiveHalted && (
        <div
          className="w-full px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-1"
          style={{ backgroundColor: 'var(--red)', color: '#fff', minHeight: '44px' }}
          role="alert"
          aria-live="polite"
        >
          <span className="font-semibold text-sm">
            ⚠ BOT HALTED
            {haltedAtStr && ` — Halted at ${haltedAtStr}`}
            {haltInfo?.source && ` ${sourceLabel(haltInfo.source)}`}
          </span>
          {haltInfo?.reason && (
            <span className="text-sm opacity-90">
              Reason: {haltInfo.reason}
            </span>
          )}
          <button
            type="button"
            onClick={() => setShowResumeModal(true)}
            className="ml-auto px-4 py-1.5 rounded font-semibold text-sm border-2 border-white hover:bg-white transition-colors"
            style={{ color: '#fff', minHeight: '44px' }}
            onMouseEnter={(e) => {
              const el = e.currentTarget
              el.style.backgroundColor = '#fff'
              el.style.color = 'var(--red)'
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget
              el.style.backgroundColor = 'transparent'
              el.style.color = '#fff'
            }}
          >
            Resume
          </button>
        </div>
      )}

      {showHaltModal && (
        <HaltModal
          onClose={() => setShowHaltModal(false)}
          onHalted={(info) => setHaltInfo(info)}
        />
      )}
      {showResumeModal && haltInfo && (
        <ResumeModal
          haltInfo={haltInfo}
          onClose={() => setShowResumeModal(false)}
          onResumed={() => setHaltInfo({ halted: false })}
        />
      )}
    </>
  )
}
