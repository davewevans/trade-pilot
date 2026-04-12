import type { ReactNode } from 'react'

interface StatCardProps {
  label: string
  value: ReactNode
  sub?: ReactNode
  trend?: 'up' | 'down' | 'flat'
  size?: 'sm' | 'md' | 'lg'
  valueColor?: string
  accent?: string
}

export function StatCard({ label, value, sub, trend, size = 'md', valueColor, accent }: StatCardProps) {
  const valueClass =
    size === 'lg' ? 'text-3xl' : size === 'sm' ? 'text-lg' : 'text-2xl'
  const trendColor =
    trend === 'up' ? 'var(--green)' :
    trend === 'down' ? 'var(--red)' :
    'var(--text-muted)'
  const arrow = trend === 'up' ? '▲' : trend === 'down' ? '▼' : ''

  return (
    <div
      className="rounded-md p-4"
      style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderTop: accent ? `3px solid ${accent}` : '1px solid var(--border)',
      }}
    >
      <div
        className="text-[12px] uppercase tracking-wider mb-1 font-semibold"
        style={{ color: 'var(--text-secondary)' }}
      >
        {label}
      </div>
      <div
        className={`${valueClass} font-mono tabular`}
        style={{ color: valueColor || 'var(--text-primary)' }}
      >
        {value}
      </div>
      {sub != null && (
        <div className="mt-1 text-xs flex items-center gap-1" style={{ color: trendColor }}>
          {arrow && <span>{arrow}</span>}
          <span>{sub}</span>
        </div>
      )}
    </div>
  )
}
