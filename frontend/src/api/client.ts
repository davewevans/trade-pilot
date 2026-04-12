import type {
  AccountSummary,
  CircuitBreaker,
  DecisionStats,
  DecisionsResponse,
  HealthStatus,
  Performance,
  Portfolio,
} from '../types'

const BASE = '' // same origin in prod; Vite proxy handles /api in dev

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`)
  return (await res.json()) as T
}

export interface DecisionsParams {
  account?: string
  underlying?: string
  action?: string
  limit?: number
}

export const api = {
  health: () => get<HealthStatus>('/api/health'),

  portfolio: (account?: string) =>
    get<Portfolio>(`/api/portfolio${account ? `?account=${account}` : ''}`),

  decisions: (params: DecisionsParams) => {
    const q = new URLSearchParams()
    if (params.account) q.set('account', params.account)
    if (params.underlying) q.set('underlying', params.underlying)
    if (params.action) q.set('action', params.action)
    if (params.limit) q.set('limit', String(params.limit))
    const qs = q.toString()
    return get<DecisionsResponse>(`/api/decisions${qs ? `?${qs}` : ''}`)
  },

  decisionStats: (account?: string) =>
    get<DecisionStats>(`/api/decisions/stats${account ? `?account=${account}` : ''}`),

  performance: (account?: string) =>
    get<Performance>(`/api/performance${account ? `?account=${account}` : ''}`),

  circuitBreakers: () => get<CircuitBreaker>('/api/circuit-breakers'),

  regimeHistory: () =>
    get<{ confirmed: string; readings: string[] }>('/api/regime-history'),

  strategyStates: () =>
    get<Record<string, { state: string; spread_id?: string }>>(
      '/api/strategy-states',
    ),
}

export const ACCOUNTS: AccountSummary[] = [
  { account: 'wheel', label: 'Wheel', strategyTypes: ['wheel'] },
  { account: 'iron_condor', label: 'Iron Condor', strategyTypes: ['iron_condor'] },
  {
    account: 'spreads',
    label: 'Spreads',
    strategyTypes: ['bull_put_spread', 'bear_call_spread', 'long_call_vertical'],
  },
]
