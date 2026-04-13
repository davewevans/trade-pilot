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

// Retry on 502/503 and network errors only. These are transient infra
// failures (Render cold starts, brief proxy hiccups) — not app errors.
// 4xx and 500 indicate real problems and should surface immediately.
async function fetchWithRetry(
  url: string,
  options?: RequestInit,
  retries = 2,
): Promise<Response> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    const backoffMs = 2 ** (attempt + 1) * 1000 // 2s, then 4s
    try {
      const res = await fetch(url, options)
      if ((res.status === 502 || res.status === 503) && attempt < retries) {
        console.warn(
          `${url} returned ${res.status}, retrying in ${backoffMs / 1000}s...`,
        )
        await new Promise((r) => setTimeout(r, backoffMs))
        continue
      }
      return res
    } catch (err) {
      if (attempt < retries) {
        console.warn(
          `${url} network error, retrying in ${backoffMs / 1000}s...`,
        )
        await new Promise((r) => setTimeout(r, backoffMs))
        continue
      }
      throw err
    }
  }
  // Unreachable: loop either returns or throws.
  throw new Error(`fetchWithRetry exhausted retries for ${url}`)
}

async function get<T>(path: string): Promise<T> {
  const res = await fetchWithRetry(`${BASE}${path}`)
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

  resetCircuitBreaker: async (): Promise<{ status: string; deleted: string[] }> => {
    const res = await fetch(`${BASE}/api/admin/reset-circuit-breaker`, {
      method: 'POST',
      credentials: 'include',
    })
    if (!res.ok) throw new Error(`API error ${res.status}: reset-circuit-breaker`)
    return (await res.json()) as { status: string; deleted: string[] }
  },

  context: () => get<ContextResponse>('/api/context'),

  equityHistory: () => get<EquityHistory>('/api/equity-history'),

  accounts: () =>
    get<Record<string, Portfolio | null>>('/api/accounts'),

  regimeHistory: () =>
    get<{ confirmed: string; readings: string[] }>('/api/regime-history'),

  strategyStates: () =>
    get<Record<string, { state: string; spread_id?: string }>>(
      '/api/strategy-states',
    ),

  watchlist: () =>
    get<{ wheel: string[]; spreads: string[]; updated_at: string | null }>('/api/watchlist'),

  updateWatchlist: async (wheel: string[], spreads: string[]) => {
    const res = await fetch(`${BASE}/api/watchlist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ wheel, spreads }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      throw new Error(err.error ?? `API error ${res.status}`)
    }
    return res.json() as Promise<{ wheel: string[]; spreads: string[]; updated_at: string | null }>
  },
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
