import type { ReactNode } from 'react'

interface StatCardProps {
  label: string
  value: ReactNode
  sub?: ReactNode
  trend?: 'up' | 'down' | 'flat'
  size?: 'sm' | 'md' | 'lg'
}

export function StatCard({ label, value, sub, trend, size = 'md' }: StatCardProps) {
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
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      <div
        className="text-[11px] uppercase tracking-wider mb-1"
        style={{ color: 'var(--text-muted)' }}
      >
        {label}
      </div>
      <div className={`${valueClass} font-mono tabular`} style={{ color: 'var(--text-primary)' }}>
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
