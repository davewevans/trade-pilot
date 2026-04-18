const MINIMUM_SAMPLE_SIZE = 15

interface InsufficientSampleBannerProps {
  decisionsScored: number
}

export function InsufficientSampleBanner({ decisionsScored }: InsufficientSampleBannerProps) {
  if (decisionsScored >= MINIMUM_SAMPLE_SIZE) return null
  return (
    <div
      className="rounded-lg px-4 py-3 mb-4 flex items-start gap-3"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--yellow) 12%, var(--bg-card))',
        border: '1px solid color-mix(in srgb, var(--yellow) 40%, var(--border))',
      }}
    >
      <span style={{ fontSize: 18, lineHeight: 1.3 }}>⚠</span>
      <div>
        <div className="text-sm font-semibold" style={{ color: 'var(--yellow)' }}>
          Insufficient sample — treat results with caution
        </div>
        <div className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
          Only {decisionsScored} decision{decisionsScored !== 1 ? 's' : ''} scored this month
          (minimum {MINIMUM_SAMPLE_SIZE} required for reliable flag detection). Scores and flags
          may not be statistically meaningful.
        </div>
      </div>
    </div>
  )
}
