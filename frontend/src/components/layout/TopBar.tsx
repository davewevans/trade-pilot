import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { CircuitBreaker, HealthStatus } from '../../types'
import { Badge } from '../shared/Badge'

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

  const dotColor = !health
    ? 'var(--text-muted)'
    : health.halted
      ? 'var(--red)'
      : 'var(--green)'

  return (
    <header
      className="flex items-center justify-between px-6 h-14 border-b"
      style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
    >
      <div className="flex items-center gap-3">
        <span
          className="inline-block w-2.5 h-2.5 rounded-full"
          style={{ backgroundColor: dotColor }}
        />
        <svg
          width="20" height="20" viewBox="0 0 24 24" fill="none"
          stroke="var(--accent)" strokeWidth="2" strokeLinecap="round"
          aria-hidden="true"
        >
          <line x1="6" y1="3" x2="6" y2="21" />
          <rect x="4" y="7" width="4" height="10" fill="var(--accent)" />
          <line x1="14" y1="3" x2="14" y2="21" />
          <rect x="12" y="11" width="4" height="8" fill="var(--green)" stroke="var(--green)" />
        </svg>
        <span className="font-semibold tracking-wide">trade-pilot</span>
        {health?.halted && <Badge variant="circuit" value="RED">HALTED</Badge>}
      </div>
      <div className="flex items-center gap-4 text-xs" style={{ color: 'var(--text-secondary)' }}>
        {cb && (
          <div className="flex items-center gap-2">
            <span className="uppercase tracking-wider opacity-75">Circuit:</span>
            <Badge variant="circuit" value={cb.status}>{cb.status}</Badge>
          </div>
        )}
        {health?.timestamp && (
          <span className="font-mono tabular">
            {new Date(health.timestamp).toLocaleTimeString()}
          </span>
        )}
      </div>
    </header>
  )
}
