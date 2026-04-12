import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { DecisionStats, Performance, Portfolio } from '../types'

export interface UseAccountResult {
  portfolio: Portfolio | null
  stats: DecisionStats | null
  performance: Performance | null
  loading: boolean
  error: string | null
}

export function useAccount(account: string): UseAccountResult {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [stats, setStats] = useState<DecisionStats | null>(null)
  const [performance, setPerformance] = useState<Performance | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        // portfolio is currently account-agnostic (single source of truth on
        // disk) — the API ignores ?account= for this endpoint today, but
        // we pass it so this just works once the server splits per account.
        const [p, s, perf] = await Promise.all([
          api.portfolio(account).catch(() => null),
          api.decisionStats(account).catch(() => null),
          api.performance(account).catch(() => null),
        ])
        if (!cancelled) {
          setPortfolio(p)
          setStats(s)
          setPerformance(perf)
        }
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

  return { portfolio, stats, performance, loading, error }
}
