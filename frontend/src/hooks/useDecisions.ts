import { useEffect, useState } from 'react'
import { api, type DecisionsParams } from '../api/client'
import type { DecisionsResponse } from '../types'

export function useDecisions(params: DecisionsParams) {
  const [data, setData] = useState<DecisionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Stable serialization of params for dependency tracking.
  const key = JSON.stringify(params)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const r = await api.decisions(params)
        if (!cancelled) setData(r)
      } catch (e) {
        if (!cancelled) setError(String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    const id = setInterval(load, 60_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return { data, loading, error }
}
