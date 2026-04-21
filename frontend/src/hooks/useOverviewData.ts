import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CircuitBreaker, HaltInfo, Portfolio, Decision } from '../types'
import type { AccountConfig, ContextResponse, HeartbeatResponse } from '../api/client'

export interface OverviewData {
  circuitBreaker: CircuitBreaker | null
  haltInfo: HaltInfo | null
  context: ContextResponse | null
  accounts: AccountConfig[]
  portfolios: Record<string, Portfolio | null>
  recentDecisions: Decision[]
  skipDecisions: Decision[]
  heartbeat: HeartbeatResponse | null
  loading: boolean
  error: string | null
  lastUpdated: Date | null
}

export function useOverviewData(): OverviewData {
  const [circuitBreaker, setCircuitBreaker] = useState<CircuitBreaker | null>(null)
  const [haltInfo, setHaltInfo] = useState<HaltInfo | null>(null)
  const [context, setContext] = useState<ContextResponse | null>(null)
  const [accounts, setAccounts] = useState<AccountConfig[]>([])
  const [portfolios, setPortfolios] = useState<Record<string, Portfolio | null>>({})
  const [recentDecisions, setRecentDecisions] = useState<Decision[]>([])
  const [skipDecisions, setSkipDecisions] = useState<Decision[]>([])
  const [heartbeat, setHeartbeat] = useState<HeartbeatResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const [cb, hi, ctx, acctResp, portfolioMap, decisions, skipDecs, hb] = await Promise.all([
          api.circuitBreakers().catch(() => null),
          api.haltStatus().catch(() => null),
          api.context().catch(() => null),
          api.accountConfig().catch(() => null),
          api.accountPortfolios().catch(() => null),
          api.decisions({ limit: 50 }).catch(() => null),
          api.decisions({ action: 'SKIP', limit: 20 }).catch(() => null),
          api.heartbeat().catch(() => null),
        ])
        if (cancelled) return

        setCircuitBreaker(cb)
        setHaltInfo(hi)
        setContext(ctx)
        setAccounts(acctResp?.accounts ?? [])
        setPortfolios(portfolioMap ?? {})
        setRecentDecisions(decisions?.decisions ?? [])
        setSkipDecisions(skipDecs?.decisions ?? [])
        setHeartbeat(hb)
        setLastUpdated(new Date())
        setError(null)
      } catch (e) {
        if (!cancelled) setError(String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    const id = setInterval(load, 30_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return {
    circuitBreaker,
    haltInfo,
    context,
    accounts,
    portfolios,
    recentDecisions,
    skipDecisions,
    heartbeat,
    loading,
    error,
    lastUpdated,
  }
}
