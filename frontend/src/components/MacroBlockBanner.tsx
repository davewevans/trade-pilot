import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { MacroBlockStatus } from '../api/client'

const POLL_INTERVAL_MS = 5 * 60 * 1000  // 5 minutes — block status changes ≤ 1× per day

function formatSessionDate(iso: string | null): string {
  if (!iso) return 'unknown'
  try {
    // noon avoids DST edge issues on date-only strings
    const d = new Date(iso + 'T12:00:00')
    return d.toLocaleDateString(undefined, {
      weekday: 'short', month: 'short', day: 'numeric',
    })
  } catch {
    return iso
  }
}

function formatEventTime(time_et: string | null): string {
  if (!time_et) return ''
  return `${time_et} ET`
}

export function MacroBlockBanner() {
  const [status, setStatus] = useState<MacroBlockStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    const fetchStatus = () => {
      api.macroBlockStatus()
        .then((s) => { if (!cancelled) setStatus(s) })
        .catch(() => { if (!cancelled) setStatus(null) })  // silent on error — banner just hides
    }
    fetchStatus()
    const id = setInterval(fetchStatus, POLL_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  if (!status?.active) return null

  const eventLabel = status.event_type ?? 'Macro event'
  const dateLabel = formatSessionDate(status.event_date)
  const timeLabel = formatEventTime(status.event_time_et)
  const clearLabel = formatSessionDate(status.next_clear_session)

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--accent-warning, #d97706) 18%, var(--bg-card))',
        borderBottom: '1px solid var(--accent-warning, #d97706)',
        color: 'var(--text-primary)',
        padding: '10px 16px',
        fontSize: '13px',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
      }}
    >
      <span aria-hidden="true" style={{ fontSize: '16px' }}>⏸</span>
      <div style={{ flex: 1 }}>
        <strong>Trading paused</strong>
        {' — '}
        {eventLabel} on {dateLabel}
        {timeLabel ? ` at ${timeLabel}` : ''}.
        {' '}
        Management cycles still run; only new entries are blocked.
        {' '}
        <span style={{ color: 'var(--text-secondary)' }}>
          Next clear session: <strong>{clearLabel}</strong>.
        </span>
      </div>
    </div>
  )
}
