import type {
  CircuitBreaker,
  ClaudeAgreementResponse,
  ClaudeCostsResponse,
  CycleSummaryResponse,
  DecisionStats,
  DecisionsResponse,
  EquityHistory,
  FillQualityResponse,
  HaltInfo,
  HealthStatus,
  NtaEventsResponse,
  Performance,
  Portfolio,
  PortfolioGreeks,
  TokenUsageDaily,
  TokenUsageSummary,
  TokenUsageToday,
  Trade,
} from '../types'

export interface AccountConfig {
  account_id: string
  label: string
  strategy: string | null
  strategy_display_name: string | null
  status: 'active' | 'inactive'
  watchlist: string[]
  screening_overrides: Record<string, unknown>
  has_credentials: boolean
}

export interface AccountConfigResponse {
  accounts: AccountConfig[]
  available_strategies: Array<{ name: string; display_name: string }>
}

export interface MacroContext {
  vix?: number | null
  fear_greed_score?: number | null
  fear_greed_rating?: string | null
}

export interface SourceHealthEntry {
  last_success: string | null
  last_failure: string | null
  last_failure_reason: string | null
  consecutive_failures: number
  today_successes: number
  today_failures: number
  last_checked: string | null
}

export interface SourceHealthResponse {
  sources: Record<string, SourceHealthEntry>
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

export interface IVHistoryPoint {
  date: string
  iv_rank_1y: number | null
  iv_rank_1m: number | null
  iv: number | null          // ATM IV as percentage (e.g. 22.5 = 22.5%)
}

export interface IVTradeMarker {
  date: string
  iv_rank: number | null
  strategy_type: string | null
  trade_type: string | null
}

export interface IVHistoryResponse {
  symbol: string
  days: number
  iv_history: IVHistoryPoint[]
  trade_markers: IVTradeMarker[]
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

function _handleUnauthorized() {
  window.dispatchEvent(new Event('auth:expired'))
}

async function get<T>(path: string): Promise<T> {
  const res = await fetchWithRetry(`${BASE}${path}`)
  if (res.status === 401) {
    _handleUnauthorized()
    throw new Error(`Unauthorized: ${path}`)
  }
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

export interface SkipBreakdownResponse {
  window_days: number;
  total_skips: number;
  by_gate: Array<{ gate: string; count: number; pct: number }>;
  by_reason_within_gate: Record<string, Array<{ reason: string; count: number; pct_of_gate: number }>>;
  unclassified_count: number;
}

export interface ScorecardCellResponse {
  outcome_type: string;
  count: number;
  recommender_correct_pct: number | null;
  avg_pnl_per_trade: number | null;
  symbols: string[];
}

export interface ScorecardResponse {
  window_days: number;
  since_days: number;
  cells: {
    accepted_add: ScorecardCellResponse;
    rejected_add: ScorecardCellResponse;
    accepted_remove: ScorecardCellResponse;
    rejected_remove: ScorecardCellResponse;
  };
  summary: {
    ground_truth_cells: string[];
    proxy_cells: string[];
    caveat: string;
  };
}

export interface ResearchLastRunResponse {
  last_run_at: string | null;
  last_scan_stats: Record<string, unknown> | null;
  most_recent_scores: string | null;
  most_recent_backtest_stats: string | null;
  most_recent_recommendation: string | null;
  most_recent_outcome: string | null;
}

export interface ORATSUsageResponse {
  recent_runs: Array<{
    run_type: string;
    started_at: string;
    completed_at: string;
    duration_seconds: number;
    call_count: number;
    by_endpoint: Record<string, number>;
  }>;
  daily_totals: Array<{ date: string; total_calls: number }>;
  this_week_total: number;
  this_month_total: number;
}

// ── Evaluation types ──────────────────────────────────────────────────────────

export type ReviewStatus = 'pending' | 'reviewed' | 'reviewed-with-action'

export interface EvaluationSummary {
  month: string
  generated_at: string
  decisions_scored: number
  closed_trades_in_window: number | null
  insufficient_sample: null  // not persisted by backend
  flag_count: number
  review_status: ReviewStatus
  judge_operator_disagreement: number | null
}

export interface EvaluationsListResponse {
  evaluations: EvaluationSummary[]
  total: number
}

export interface DimensionStats {
  mean: number
  median: number
  stddev: number
  n: number
  by_prompt_version?: Record<string, { mean: number; median: number; stddev: number; n: number }>
}

export interface StrategyDistribution {
  by_dimension: Record<string, DimensionStats>
  decisions_scored: number
  closed_trades_in_window: number
}

export interface ScoreDistribution {
  by_strategy: Record<string, StrategyDistribution>
  overall: {
    by_dimension: Record<string, DimensionStats>
    decisions_scored: number
    closed_trades_in_window: number
  }
}

export interface FlagSummaryItem {
  strategy: string
  dimension: string
  severity: string
  reason: string
}

export interface EvaluationDetailResponse {
  month: string
  generated_at: string
  decisions_scored: number
  avg_score: number | null
  pct_pass: number | null
  reviewed_at: string | null
  action_note: string | null
  review_status: ReviewStatus
  flag_summary: FlagSummaryItem[]
  score_distribution: ScoreDistribution
  judge_operator_disagreement: number | null
}

export interface FlaggedDecision {
  id: number
  timestamp: string
  underlying: string | null
  action: string | null
  reasoning: unknown
  context: unknown
  scores: Array<{
    id: number
    decision_id: number
    scorer_type: string
    total_score: number | null
    dimension_scores_json: string | null
    dimension_scores?: Array<{ dimension: string; score: number; justification?: string }>
    scored_at: string
    prompt_version: string | null
  }>
  [key: string]: unknown
}

export interface FlaggedDecisionGroup {
  strategy: string
  dimension: string
  severity: string
  reason: string
  decisions: FlaggedDecision[]
}

export interface FlaggedDecisionsResponse {
  month: string
  flags: FlaggedDecisionGroup[]
}

export interface MarkReviewedResponse {
  month: string
  review_status: ReviewStatus
  reviewed_at: string | null
  action_note: string | null
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
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) throw new Error(`API error ${res.status}: reset-circuit-breaker`)
    return (await res.json()) as { status: string; deleted: string[] }
  },

  context: () => get<ContextResponse>('/api/context'),

  equityHistory: () => get<EquityHistory>('/api/equity-history'),

  accountPortfolios: () =>
    get<Record<string, Portfolio | null>>('/api/account-portfolios'),

  accountConfig: () =>
    get<AccountConfigResponse>('/api/accounts'),

  activateAccount: async (id: string): Promise<{ account_id: string; status: string }> => {
    const res = await fetch(`${BASE}/api/accounts/${encodeURIComponent(id)}/activate`, {
      method: 'POST',
      credentials: 'include',
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      throw new Error(err.error ?? `API error ${res.status}`)
    }
    return res.json()
  },

  deactivateAccount: async (id: string): Promise<{ account_id: string; status: string }> => {
    const res = await fetch(`${BASE}/api/accounts/${encodeURIComponent(id)}/deactivate`, {
      method: 'POST',
      credentials: 'include',
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      throw new Error(err.error ?? `API error ${res.status}`)
    }
    return res.json()
  },

  linkStrategy: async (id: string, strategy: string): Promise<{ account_id: string; strategy: string }> => {
    const res = await fetch(`${BASE}/api/accounts/${encodeURIComponent(id)}/link-strategy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ strategy }),
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      throw new Error(err.error ?? `API error ${res.status}`)
    }
    return res.json()
  },

  updateAccountWatchlist: async (id: string, watchlist: string[]): Promise<{ account_id: string; watchlist: string[] }> => {
    const res = await fetch(`${BASE}/api/accounts/${encodeURIComponent(id)}/watchlist`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ watchlist }),
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      throw new Error(err.error ?? `API error ${res.status}`)
    }
    return res.json()
  },

  regimeHistory: () =>
    get<{ confirmed: string; readings: string[] }>('/api/regime-history'),

  strategyStates: () =>
    get<Record<string, { state: string; spread_id?: string }>>(
      '/api/strategy-states',
    ),

  sourceHealth: () => get<SourceHealthResponse>('/api/source-health'),

  startBacktest: async (params: Record<string, unknown>): Promise<{ job_id: string; status: string }> => {
    const res = await fetch(`${BASE}/api/backtest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(params),
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      throw new Error(err.error ?? `API error ${res.status}`)
    }
    return res.json()
  },

  getBacktestJob: (jobId: string) =>
    get<{ job_id: string; status: string; progress: string; error?: string | null; result?: unknown }>(
      `/api/backtest/${jobId}`,
    ),

  ivHistory: (symbol: string, days: number = 365) =>
    get<IVHistoryResponse>(`/api/iv-history?symbol=${encodeURIComponent(symbol)}&days=${days}`),

  fillQuality: (params?: { account?: string; days?: number }) => {
    const search = new URLSearchParams()
    if (params?.account) search.set('account', params.account)
    if (params?.days) search.set('days', String(params.days))
    const qs = search.toString()
    return get<FillQualityResponse>(`/api/fill-quality${qs ? '?' + qs : ''}`)
  },

  ntaEvents: (days?: number) => {
    const qs = days ? `?days=${days}` : ''
    return get<NtaEventsResponse>(`/api/nta-events${qs}`)
  },

  pendingCount: () => get<{ count: number }>('/api/pending-count'),

  tokenUsageToday: () => get<TokenUsageToday>('/api/token-usage/today'),
  tokenUsageSummary: () => get<TokenUsageSummary>('/api/token-usage/summary'),
  tokenUsageDaily: (days: number = 30) =>
    get<{ daily: TokenUsageDaily[] }>(`/api/token-usage?days=${days}`),

  skipBreakdown: (account?: string, sinceDays?: number): Promise<SkipBreakdownResponse> => {
    const q = new URLSearchParams()
    if (account) q.set('account', account)
    if (sinceDays != null) q.set('since_days', String(sinceDays))
    const qs = q.toString()
    return get<SkipBreakdownResponse>(`/api/decisions/skip-breakdown${qs ? `?${qs}` : ''}`)
  },

  recommendationScorecard: (windowDays?: number, sinceDays?: number): Promise<ScorecardResponse> => {
    const q = new URLSearchParams()
    if (windowDays != null) q.set('window_days', String(windowDays))
    if (sinceDays != null) q.set('since_days', String(sinceDays))
    const qs = q.toString()
    return get<ScorecardResponse>(`/api/research/recommendations/scorecard${qs ? `?${qs}` : ''}`)
  },

  researchLastRun: (): Promise<ResearchLastRunResponse> =>
    get<ResearchLastRunResponse>('/api/research/last-run'),

  oratsUsage: (): Promise<ORATSUsageResponse> =>
    get<ORATSUsageResponse>('/api/orats/usage'),

  listEvaluations: (limit = 20, offset = 0): Promise<EvaluationsListResponse> => {
    const q = new URLSearchParams()
    q.set('limit', String(limit))
    q.set('offset', String(offset))
    return get<EvaluationsListResponse>(`/api/evaluations?${q.toString()}`)
  },

  getEvaluation: (month: string): Promise<EvaluationDetailResponse> =>
    get<EvaluationDetailResponse>(`/api/evaluations/${encodeURIComponent(month)}`),

  getFlaggedDecisions: (month: string): Promise<FlaggedDecisionsResponse> =>
    get<FlaggedDecisionsResponse>(`/api/evaluations/${encodeURIComponent(month)}/flagged-decisions`),

  markReviewed: async (month: string, actionNote: string | null): Promise<MarkReviewedResponse> => {
    const res = await fetchWithRetry(`${BASE}/api/evaluations/${encodeURIComponent(month)}/mark-reviewed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ action_note: actionNote }),
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      throw new Error(err.error ?? `API error ${res.status}`)
    }
    return res.json() as Promise<MarkReviewedResponse>
  },

  claudeCosts: (window: '7d' | '30d' | '90d' | 'all' = '7d', account?: string): Promise<ClaudeCostsResponse> => {
    const q = new URLSearchParams()
    q.set('window', window)
    if (account) q.set('account', account)
    return get<ClaudeCostsResponse>(`/api/claude-costs?${q.toString()}`)
  },

  cycleSummary: (jobRunId?: string): Promise<CycleSummaryResponse> => {
    const qs = jobRunId ? `?job_run_id=${encodeURIComponent(jobRunId)}` : ''
    return get<CycleSummaryResponse>(`/api/cycle-summary${qs}`)
  },

  claudeAgreement: (window: '7d' | '30d' | '90d' | 'all' = '30d'): Promise<ClaudeAgreementResponse> => {
    return get<ClaudeAgreementResponse>(`/api/claude-agreement?window=${window}`)
  },

  portfolioGreeks: (account?: string): Promise<PortfolioGreeks> => {
    const qs = account ? `?account=${encodeURIComponent(account)}` : ''
    return get<PortfolioGreeks>(`/api/portfolio-greeks${qs}`)
  },

  haltStatus: () => get<HaltInfo>('/api/halt-status'),

  halt: async (reason?: string): Promise<HaltInfo> => {
    const res = await fetchWithRetry(`${BASE}/api/halt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ reason: reason ?? '' }),
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    // 200 = halted now, 409 = already halted — both return the current halt info
    return (await res.json()) as HaltInfo
  },

  resume: async (): Promise<HaltInfo> => {
    const res = await fetchWithRetry(`${BASE}/api/resume`, {
      method: 'POST',
      credentials: 'include',
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) throw new Error(`API error ${res.status}: resume`)
    return (await res.json()) as HaltInfo
  },

  watchlist: () =>
    get<{ wheel: string[]; iron_condor: string[]; spreads: string[]; updated_at: string | null }>('/api/watchlist'),

  updateWatchlist: async (wheel: string[], iron_condor: string[], spreads: string[]) => {
    const res = await fetch(`${BASE}/api/watchlist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ wheel, iron_condor, spreads }),
    })
    if (res.status === 401) { _handleUnauthorized(); throw new Error('Unauthorized') }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      throw new Error(err.error ?? `API error ${res.status}`)
    }
    return res.json() as Promise<{ wheel: string[]; iron_condor: string[]; spreads: string[]; updated_at: string | null }>
  },
}

