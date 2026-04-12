import type { ReactNode } from 'react'

type IconName = 'clock' | 'chart' | 'inbox' | 'wheel' | 'list'

interface EmptyStateProps {
  message: string
  hint?: string
  icon?: IconName
}

function Icon({ name }: { name: IconName }) {
  const common = {
    width: 28,
    height: 28,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }
  switch (name) {
    case 'clock':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
      )
    case 'chart':
      return (
        <svg {...common}>
          <path d="M3 3v18h18" />
          <path d="M7 15l4-5 3 3 5-7" />
        </svg>
      )
    case 'wheel':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M5.6 18.4L18.4 5.6" />
        </svg>
      )
    case 'list':
      return (
        <svg {...common}>
          <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
        </svg>
      )
    case 'inbox':
    default:
      return (
        <svg {...common}>
          <path d="M22 13H16l-2 3h-4l-2-3H2" />
          <path d="M5.45 5.11L2 13v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.45-7.89A2 2 0 0016.76 4H7.24a2 2 0 00-1.79 1.11z" />
        </svg>
      )
  }
}

export function EmptyState({ message, hint, icon }: EmptyStateProps): ReactNode {
  return (
    <div
      className="text-center py-10 rounded flex flex-col items-center"
      style={{
        color: 'var(--text-muted)',
        backgroundColor: 'var(--bg-card)',
        border: '1px dashed var(--border)',
      }}
    >
      {icon && (
        <div className="mb-3 opacity-60">
          <Icon name={icon} />
        </div>
      )}
      <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>{message}</div>
      {hint && <div className="text-xs mt-1 opacity-75">{hint}</div>}
    </div>
  )
}
