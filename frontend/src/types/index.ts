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
}

export interface CircuitBreaker {
  status: 'GREEN' | 'YELLOW' | 'RED'
  halted: boolean
  daily_pnl: number
  daily_pnl_pct: number
  weekly_pnl: number
  weekly_pnl_pct: number
  current_drawdown_pct: number
}
