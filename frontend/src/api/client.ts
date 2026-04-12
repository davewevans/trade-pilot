import type {
  AccountSummary,
  CircuitBreaker,
  DecisionStats,
  DecisionsResponse,
  EquityHistory,
  HealthStatus,
  Performance,
  Portfolio,
  Trade,
} from '../types'

export interface MacroContext {
  vix?: number | null
  fear_greed_score?: number | null
  fear_greed_rating?: string | null
}

export interface ContextResponse {
  macro?: MacroContext
  confirmed_market_regime?: string | null
  [key: string]: unknown
}

export interface TradesResponse {
  trades: Trade[]
  total: number
}

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
  offset?: number
  // 0.9 / 0.6 / 0.3 — converted from HIGH/MEDIUM/LOW UI labels
  confidence?: number
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
    if (params.offset) q.set('offset', String(params.offset))
    if (params.confidence != null) q.set('confidence', String(params.confidence))
    const qs = q.toString()
    return get<DecisionsResponse>(`/api/decisions${qs ? `?${qs}` : ''}`)
  },

  decisionStats: (account?: string) =>
    get<DecisionStats>(`/api/decisions/stats${account ? `?account=${account}` : ''}`),

  performance: (account?: string) =>
    get<Performance>(`/api/performance${account ? `?account=${account}` : ''}`),

  trades: (params: { account?: string; underlying?: string; limit?: number }) => {
    const q = new URLSearchParams()
    if (params.account) q.set('account', params.account)
    if (params.underlying) q.set('underlying', params.underlying)
    if (params.limit) q.set('limit', String(params.limit))
    const qs = q.toString()
    return get<TradesResponse>(`/api/trades${qs ? `?${qs}` : ''}`)
  },

  circuitBreakers: () => get<CircuitBreaker>('/api/circuit-breakers'),

  context: () => get<ContextResponse>('/api/context'),

  equityHistory: () => get<EquityHistory>('/api/equity-history'),

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
