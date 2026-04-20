import { useEffect, useState } from 'react'
import { api } from '../api/client'

export interface FeatureFlags {
  strategy_health_enabled: boolean
  shadow_execution_enabled: boolean
}

const DEFAULTS: FeatureFlags = {
  strategy_health_enabled: true,
  shadow_execution_enabled: true,
}

/**
 * Fetches feature flags from /api/health (polled every 30s by TopBar).
 * Defaults to all features enabled until the response arrives so the
 * sidebar renders immediately on fast connections.
 */
export function useFeatureFlags(): FeatureFlags {
  const [flags, setFlags] = useState<FeatureFlags>(DEFAULTS)

  useEffect(() => {
    api
      .health()
      .then((h) => {
        setFlags({
          strategy_health_enabled: h.strategy_health_enabled ?? true,
          shadow_execution_enabled: h.shadow_execution_enabled ?? true,
        })
      })
      .catch(() => {/* keep defaults */})
  }, [])

  return flags
}
