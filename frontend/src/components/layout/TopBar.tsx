import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { CircuitBreaker, HealthStatus } from '../../types'
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

export function TopBar() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [cb, setCb] = useState<CircuitBreaker | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      const [h, c] = await Promise.all([
        api.health().catch(() => null),
        api.circuitBreakers().catch(() => null),
      ])
      if (!cancelled) {
        setHealth(h)
        setCb(c)
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
  const effectiveHalted = Boolean(health?.halted) && !dryRun

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

  return (
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
        <img
          src="/favicon.svg"
          alt=""
          width={36}
          height={36}
          aria-hidden="true"
        />
        <span className="font-semibold tracking-wide">trade-pilot</span>
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
  )
}
