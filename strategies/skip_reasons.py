"""Skip gate and reason enums for classifying decision skips by gate."""

from enum import Enum


class SkipGate(str, Enum):
    PRE_CHECK = "pre_check"
    GUARDRAIL = "guardrail"
    CIRCUIT_BREAKER = "circuit_breaker"
    MACRO_EVENT = "macro_event"
    LIQUIDITY_FLOOR = "liquidity_floor"
    WINRATE_FLOOR = "winrate_floor"
    CLAUDE_SKIP = "claude_skip"
    LLM_OUTPUT = "llm_output"
    NO_CANDIDATE = "no_candidate"
    DATA_MISSING = "data_missing"
    HALTED = "halted"
    PORTFOLIO = "portfolio"


class SkipReason(str, Enum):
    # pre_check family
    EARNINGS_TOO_CLOSE = "earnings_too_close"
    IVR_TOO_LOW = "ivr_too_low"
    IVR_TOO_HIGH = "ivr_too_high"
    WRONG_REGIME = "wrong_regime"
    WRONG_IV_ENV = "wrong_iv_env"
    DUPLICATE_POSITION = "duplicate_position"
    STOCK_BELOW_SMA = "stock_below_sma"
    STOCK_ABOVE_SMA = "stock_above_sma"
    DAYS_TO_EX_DIV_TOO_CLOSE = "days_to_ex_div_too_close"
    # candidate family
    NO_CANDIDATES_FOUND = "no_candidates_found"
    CREDIT_TOO_LOW = "credit_too_low"
    DEBIT_OUT_OF_RANGE = "debit_out_of_range"
    CREDIT_WIDTH_RATIO_TOO_LOW = "credit_width_ratio_too_low"
    LIQUIDITY_LOW = "liquidity_low"
    # research-layer rejects
    LIQUIDITY_TIER_D = "liquidity_tier_d"
    WINRATE_BELOW_FLOOR = "winrate_below_floor"
    # guardrail family
    GUARDRAIL_POSITION_SIZE = "guardrail_position_size"
    GUARDRAIL_EARNINGS = "guardrail_earnings"
    GUARDRAIL_SYMBOL_FORMAT = "guardrail_symbol_format"
    GUARDRAIL_SHARED_CAPITAL = "guardrail_shared_capital"
    GUARDRAIL_OTHER = "guardrail_other"
    # macro event
    MACRO_EVENT_PROXIMITY = "macro_event_proximity"
    # system
    CIRCUIT_BREAKER_RED = "circuit_breaker_red"
    CIRCUIT_BREAKER_YELLOW = "circuit_breaker_yellow"
    BOT_HALTED = "bot_halted"
    DROP_COPY_BLOCK = "drop_copy_block"
    CLAUDE_SKIP = "claude_skip"
    CLAUDE_ERROR = "claude_error"
    SCHEMA_INVALID = "schema_invalid"
    DATA_MISSING = "data_missing"
    UNKNOWN = "unknown"
    ANTI_CROWDING_CROSS_ACCOUNT = "anti_crowding_cross_account"


REASON_TO_GATE = {
    SkipReason.EARNINGS_TOO_CLOSE: SkipGate.PRE_CHECK,
    SkipReason.IVR_TOO_LOW: SkipGate.PRE_CHECK,
    SkipReason.IVR_TOO_HIGH: SkipGate.PRE_CHECK,
    SkipReason.WRONG_REGIME: SkipGate.PRE_CHECK,
    SkipReason.WRONG_IV_ENV: SkipGate.PRE_CHECK,
    SkipReason.DUPLICATE_POSITION: SkipGate.PRE_CHECK,
    SkipReason.STOCK_BELOW_SMA: SkipGate.PRE_CHECK,
    SkipReason.STOCK_ABOVE_SMA: SkipGate.PRE_CHECK,
    SkipReason.DAYS_TO_EX_DIV_TOO_CLOSE: SkipGate.PRE_CHECK,
    SkipReason.NO_CANDIDATES_FOUND: SkipGate.NO_CANDIDATE,
    SkipReason.CREDIT_TOO_LOW: SkipGate.PRE_CHECK,
    SkipReason.DEBIT_OUT_OF_RANGE: SkipGate.PRE_CHECK,
    SkipReason.CREDIT_WIDTH_RATIO_TOO_LOW: SkipGate.PRE_CHECK,
    SkipReason.LIQUIDITY_LOW: SkipGate.PRE_CHECK,
    SkipReason.LIQUIDITY_TIER_D: SkipGate.LIQUIDITY_FLOOR,
    SkipReason.WINRATE_BELOW_FLOOR: SkipGate.WINRATE_FLOOR,
    SkipReason.GUARDRAIL_POSITION_SIZE: SkipGate.GUARDRAIL,
    SkipReason.GUARDRAIL_EARNINGS: SkipGate.GUARDRAIL,
    SkipReason.GUARDRAIL_SYMBOL_FORMAT: SkipGate.GUARDRAIL,
    SkipReason.GUARDRAIL_SHARED_CAPITAL: SkipGate.GUARDRAIL,
    SkipReason.GUARDRAIL_OTHER: SkipGate.GUARDRAIL,
    SkipReason.MACRO_EVENT_PROXIMITY: SkipGate.MACRO_EVENT,
    SkipReason.CIRCUIT_BREAKER_RED: SkipGate.CIRCUIT_BREAKER,
    SkipReason.CIRCUIT_BREAKER_YELLOW: SkipGate.CIRCUIT_BREAKER,
    SkipReason.BOT_HALTED: SkipGate.HALTED,
    SkipReason.DROP_COPY_BLOCK: SkipGate.CIRCUIT_BREAKER,
    SkipReason.CLAUDE_SKIP: SkipGate.CLAUDE_SKIP,
    SkipReason.CLAUDE_ERROR: SkipGate.CLAUDE_SKIP,
    SkipReason.SCHEMA_INVALID: SkipGate.LLM_OUTPUT,
    SkipReason.DATA_MISSING: SkipGate.DATA_MISSING,
    SkipReason.UNKNOWN: SkipGate.PRE_CHECK,
    SkipReason.ANTI_CROWDING_CROSS_ACCOUNT: SkipGate.PORTFOLIO,
}
