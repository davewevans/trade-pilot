import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Performance } from '../types'

export function usePerformance(account?: string) {
  const [data, setData] = useState<Performance | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const r = await api.performance(account)
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
  }, [account])

  return { data, loading, error }
}
