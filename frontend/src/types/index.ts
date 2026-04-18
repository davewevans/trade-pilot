export interface AccountSummary {
  account: string
  label: string
  strategyTypes: string[]
}

export interface Position {
  symbol: string
  underlying: string
  strategy_type: string
  strike: number
  expiration: string
  dte: number
  quantity: number
  entry_credit: number
  current_value: number
  unrealized_pnl: number
  delta?: number
  theta?: number
}

export interface EquityHistoryPoint {
  date: string
  equity: number
  pnl: number
  pnl_pct: number
}

export interface EquityHistory {
  timestamp: string
  base_value: number
  timeframe: string
  points: EquityHistoryPoint[]
}

export interface Portfolio {
  account: {
    total_equity: number
    buying_power: number
    cash: number
    last_equity?: number
    today_pnl?: number
    today_pnl_pct?: number
    buying_power_used_pct?: number
  }
  positions: Position[]
  wheel_states: Record<string, string>
  timestamp: string
}

export interface DecisionReasoning {
  macro?: string
  fundamental?: string
  technical?: string
  volatility?: string
  selection?: string
  risk?: string
}

export interface Decision {
  id: number
  // The DB column is `timestamp`; the spec aliases it as `created_at` for the
  // response. The repository surfaces both keys (`timestamp` is the raw column).
  created_at?: string
  timestamp?: string
  underlying: string
  strategy_type: string
  action: string
  confidence: number | string | null
  // The reasoning column is a free-form string in DB but Claude returns a
  // 6-field object from the wheel prompt. We accept either shape and the UI
  // detects which.
  reasoning: DecisionReasoning | string | null
  skip_reason?: string | null
  cycle_id: string | null
}

export interface DecisionsResponse {
  decisions: Decision[]
  total: number
}

export interface DecisionStats {
  total_decisions: number
  trades: number
  skips: number
  closes?: number
  rolls?: number
  win_rate: number
  avg_iv_rank_at_entry: number | null
  decisions_by_underlying: Record<string, number>
  skip_reasons: Record<string, number>
}

export interface Trade {
  id: number
  filled_at: string | null
  submitted_at: string
  underlying: string
  strategy_type: string
  trade_type: string
  symbol: string
  limit_price?: number | null
  fill_price: number | null
  contracts: number
  pnl: number
}

export interface EquityPoint {
  date: string
  cumulative_pnl: number
}

export interface Performance {
  total_pnl: number
  total_pnl_pct?: number
  realized_pnl: number
  unrealized_pnl?: number
  equity_curve: EquityPoint[]
  best_trade: Trade | null
  worst_trade: Trade | null
}

export interface HealthStatus {
  status: string
  timestamp: string
  halted: boolean
  version?: string
  version_date?: string
  strategy_health_enabled?: boolean
}

export interface HaltInfo {
  halted: boolean
  halted_at?: string | null
  source?: string
  reason?: string
}

export interface FillQualityResponse {
  trades_analyzed: number
  avg_slippage: number
  median_slippage: number
  total_slippage_dollars: number
  positive_slippage_count: number
  negative_slippage_count: number
  exact_fill_count: number
  worst_slippage: number
  best_slippage: number
  recent_fills: Array<{
    symbol: string
    underlying: string
    strategy_type: string
    limit_price: number
    fill_price: number
    slippage: number
    filled_at: string
  }>
}

export interface NtaEvent {
  type: 'assignment' | 'expiry' | 'exercise'
  symbol: string
  underlying: string
  qty: number
  price: number | null
  date: string
  raw_type: string
}

export interface NtaEventsResponse {
  events: NtaEvent[]
  total: number
  error?: string
}

export interface CircuitBreaker {
  status: 'GREEN' | 'YELLOW' | 'RED'
  halted: boolean
  daily_pnl: number
  daily_pnl_pct: number
  weekly_pnl: number
  weekly_pnl_pct: number
  current_drawdown_pct: number
  dry_run?: boolean
}

export interface ClaudeCostOutcomeBucket {
  count: number
  cost_usd: number
}

export interface ClaudeCostsResponse {
  window: string
  total_cost_usd: number
  prior_window_cost_usd: number
  decisions_count: number
  filled_trades_count: number
  cost_per_filled_trade_usd: number | null
  cost_by_outcome: {
    open: ClaudeCostOutcomeBucket
    close: ClaudeCostOutcomeBucket
    skip: ClaudeCostOutcomeBucket
  }
  cache_hit_rate: number | null
  by_model_version: Record<string, { count: number; cost_usd: number }>
}

export interface TokenUsageToday {
  date: string
  total_input: number
  total_output: number
  total_cache_read: number
  total_cache_creation: number
  calls_count: number
  avg_response_ms: number
  estimated_cost_usd: number
  cache_hit_rate: number
}

export interface TokenUsageDaily {
  date: string
  total_input: number
  total_output: number
  calls_count: number
  estimated_cost_usd: number
  cache_hit_rate: number
}

export interface TokenUsageSummary {
  lifetime: {
    total_calls: number
    total_input: number
    total_output: number
    total_cache_read: number
    total_cache_creation: number
    total_cost_usd: number
    avg_cost_per_call: number
    overall_cache_hit_rate: number
    first_recorded: string | null
    last_recorded: string | null
  }
  by_strategy: Array<{
    strategy_type: string
    calls: number
    total_cost_usd: number
    avg_cost_per_call: number
    cache_hit_rate: number
  }>
  prompt_size: {
    system_prompt_tokens: number | null
    context_window: number
    utilization_pct: number | null
    measured_at: string
  } | null
}

export interface CycleSummaryOrder {
  symbol: string
  strategy: string
  action: string
  contract: string | null
  limit_price: number | null
}

export interface CycleSummaryResponse {
  job_run_id: string | null
  cycle_started_at: string | null
  cycle_type: string
  symbols_evaluated: number
  pre_check_skipped: {
    total: number
    by_reason: Record<string, number>
  }
  sent_to_claude: number
  claude_outcomes: {
    SKIP: number
    OPEN: number
    CLOSE: number
    HOLD: number
  }
  guardrail_rejections: {
    total: number
    by_rule: Record<string, number>
  }
  orders_placed: number
  orders_details: CycleSummaryOrder[]
}

export interface PortfolioGreeksBucket {
  theta: number
  vega: number
  position_count: number
}

export interface PortfolioGreeksByStrategy {
  net_delta: number
  net_theta: number
  net_vega: number
  position_count: number
}

export interface PortfolioGreeks {
  net_delta: number
  net_theta: number
  net_vega: number
  net_gamma: number
  total_defined_risk_usd: number
  position_count: number
  by_dte_bucket: {
    '0_7': PortfolioGreeksBucket
    '8_21': PortfolioGreeksBucket
    '22_45': PortfolioGreeksBucket
    '46_plus': PortfolioGreeksBucket
  }
  by_strategy: Record<string, PortfolioGreeksByStrategy>
  freshness: {
    oldest_contract_age_seconds: number
    newest_contract_age_seconds: number
    max_skew_seconds: number
    contracts_from_fallback_source: number
    computed_at: string
  }
}

export interface ClaudeAgreementDailyPoint {
  date: string
  skip_when_open_rate: number | null
  sample_size: number
}

export interface ClaudeAgreementResponse {
  window: string
  decisions_evaluated: number
  precheck_open_count: number
  claude_skip_rate_when_precheck_says_open: number | null
  precheck_skip_count: number
  claude_open_rate_when_precheck_says_skip: number | null
  daily_series: ClaudeAgreementDailyPoint[]
}
