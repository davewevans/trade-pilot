interface Props {
  last_updated: string | null;
  stale_after_days?: number;
  label?: string;
}

export function StalenessBadge({ last_updated, stale_after_days = 14, label }: Props) {
  if (!last_updated) {
    return (
      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
        {label ? `Last ${label}: ` : ''}No data
      </span>
    )
  }

  const now = new Date()
  const updated = new Date(last_updated)
  const diffMs = now.getTime() - updated.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  const prefix = label ? `Last ${label}: ` : ''

  if (diffDays <= 0) {
    return (
      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
        {prefix}Updated today
      </span>
    )
  }

  if (diffDays <= stale_after_days) {
    return (
      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
        {prefix}Updated {diffDays}d ago
      </span>
    )
  }

  return (
    <span
      className="text-xs px-2 py-0.5 rounded"
      style={{ backgroundColor: 'var(--red)', color: 'white' }}
    >
      {prefix}Stale — {diffDays}d old
    </span>
  )
}
