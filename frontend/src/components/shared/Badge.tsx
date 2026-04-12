import type { ReactNode } from 'react'

type Variant = 'action' | 'confidence' | 'regime' | 'circuit' | 'neutral'

interface BadgeProps {
  variant?: Variant
  value?: string | number | null
  children?: ReactNode
}

function actionColor(action: string): string {
  const a = action.toUpperCase()
  if (a.startsWith('SELL')) return 'var(--green)'
  if (a === 'OPEN') return 'var(--green)'
  if (a === 'ROLL') return 'var(--yellow)'
  if (a === 'CLOSE') return 'var(--blue)'
  if (a === 'SKIP' || a === 'HOLD') return 'var(--text-muted)'
  return 'var(--text-muted)'
}

function confidenceMeta(value: string | number | null | undefined) {
  if (value == null) return { label: 'N/A', color: 'var(--text-muted)' }
  let label: string
  let color: string
  const n = typeof value === 'number' ? value : null
  if (n != null) {
    if (n >= 0.75) { label = 'HIGH'; color = 'var(--green)' }
    else if (n >= 0.5) { label = 'MEDIUM'; color = 'var(--yellow)' }
    else { label = 'LOW'; color = 'var(--red)' }
  } else {
    const s = String(value).toUpperCase()
    label = s
    color =
      s === 'HIGH' ? 'var(--green)' :
      s === 'MEDIUM' ? 'var(--yellow)' :
      s === 'LOW' ? 'var(--red)' :
      'var(--text-muted)'
  }
  return { label, color }
}

function regimeColor(r: string): string {
  const s = r.toUpperCase()
  if (s === 'BULL') return 'var(--green)'
  if (s === 'BEAR') return 'var(--red)'
  if (s === 'CRASH') return 'var(--red)'
  if (s === 'EUPHORIA') return 'var(--yellow)'
  return 'var(--text-muted)'
}

function circuitColor(s: string): string {
  const u = s.toUpperCase()
  if (u === 'GREEN') return 'var(--green)'
  if (u === 'YELLOW') return 'var(--yellow)'
  if (u === 'RED') return 'var(--red)'
  return 'var(--text-muted)'
}

export function Badge({ variant = 'neutral', value, children }: BadgeProps) {
  const text = String(children ?? value ?? '')
  let bg = 'var(--text-muted)'
  let display = text

  if (variant === 'action') bg = actionColor(text)
  else if (variant === 'confidence') {
    const meta = confidenceMeta(value ?? text)
    bg = meta.color
    display = meta.label
  }
  else if (variant === 'regime') bg = regimeColor(text)
  else if (variant === 'circuit') bg = circuitColor(text)

  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider"
      style={{
        backgroundColor: `color-mix(in srgb, ${bg} 18%, transparent)`,
        color: bg,
        border: `1px solid color-mix(in srgb, ${bg} 35%, transparent)`,
      }}
    >
      {display}
    </span>
  )
}
