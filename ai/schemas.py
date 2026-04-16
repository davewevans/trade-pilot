"""JSON schemas for structured outputs — one schema per (strategy, phase).

These are passed as ``output_config.format.schema`` on every Anthropic API
call. Claude is physically incapable of returning output that violates the
schema, so no parse-retry loop or markdown-fence stripping is needed.

Design decisions documented in docs/structured_outputs_design.md.

Usage:
    schema = get_schema("wheel", "idle")
    schema = get_schema("bull_put_spread", "open")
"""

# ── Shared sub-schemas ────────────────────────────────────────────────────────

_REASONING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["macro", "fundamental", "technical", "volatility", "selection", "risk"],
    "properties": {
        "macro":       {"type": "string"},
        "fundamental": {"type": "string"},
        "technical":   {"type": "string"},
        "volatility":  {"type": "string"},
        "selection":   {"type": "string"},
        "risk":        {"type": "string"},
    },
}


# ── Wheel schemas ─────────────────────────────────────────────────────────────

WHEEL_IDLE = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "symbol", "qty", "order_type", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":      {"type": "string", "enum": ["sell_put", "skip"]},
        "symbol":      {"type": ["string", "null"]},
        "qty":         {"type": "integer", "minimum": 1},
        "order_type":  {"type": "string", "enum": ["limit", "market"]},
        "limit_price": {"type": ["number", "null"]},
        "reasoning":   _REASONING_SCHEMA,
        "confidence":  {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason": {"type": ["string", "null"]},
    },
}

WHEEL_SHORT_PUT = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "symbol", "qty", "order_type", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":      {"type": "string", "enum": ["roll", "close", "hold", "skip"]},
        "symbol":      {"type": ["string", "null"]},
        "qty":         {"type": "integer", "minimum": 1},
        "order_type":  {"type": "string", "enum": ["limit", "market"]},
        "limit_price": {"type": ["number", "null"]},
        "reasoning":   _REASONING_SCHEMA,
        "confidence":  {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason": {"type": ["string", "null"]},
    },
}

WHEEL_LONG_STOCK = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "symbol", "qty", "order_type", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":      {"type": "string", "enum": ["sell_call", "close", "hold", "skip"]},
        "symbol":      {"type": ["string", "null"]},
        "qty":         {"type": "integer", "minimum": 1},
        "order_type":  {"type": "string", "enum": ["limit", "market"]},
        "limit_price": {"type": ["number", "null"]},
        "reasoning":   _REASONING_SCHEMA,
        "confidence":  {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason": {"type": ["string", "null"]},
    },
}

WHEEL_SHORT_CALL = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "symbol", "qty", "order_type", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":      {"type": "string", "enum": ["roll", "close", "hold", "skip"]},
        "symbol":      {"type": ["string", "null"]},
        "qty":         {"type": "integer", "minimum": 1},
        "order_type":  {"type": "string", "enum": ["limit", "market"]},
        "limit_price": {"type": ["number", "null"]},
        "reasoning":   _REASONING_SCHEMA,
        "confidence":  {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason": {"type": ["string", "null"]},
    },
}


# ── Spread idle schemas (entry decisions) ─────────────────────────────────────

BULL_PUT_SPREAD_IDLE = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "short_put_symbol", "long_put_symbol", "expiration",
        "dte", "short_put_strike", "long_put_strike",
        "net_credit", "max_loss", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":           {"type": "string", "enum": ["OPEN", "SKIP"]},
        "short_put_symbol": {"type": ["string", "null"]},
        "long_put_symbol":  {"type": ["string", "null"]},
        "expiration":       {"type": ["string", "null"]},
        "dte":              {"type": ["integer", "null"], "minimum": 0},
        "short_put_strike": {"type": ["number", "null"]},
        "long_put_strike":  {"type": ["number", "null"]},
        "net_credit":       {"type": ["number", "null"]},
        "max_loss":         {"type": ["number", "null"]},
        "limit_price":      {"type": ["number", "null"]},
        "reasoning":        _REASONING_SCHEMA,
        "confidence":       {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason":      {"type": ["string", "null"]},
    },
}

BEAR_CALL_SPREAD_IDLE = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "short_call_symbol", "long_call_symbol", "expiration",
        "dte", "short_call_strike", "long_call_strike",
        "net_credit", "max_loss", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":            {"type": "string", "enum": ["OPEN", "SKIP"]},
        "short_call_symbol": {"type": ["string", "null"]},
        "long_call_symbol":  {"type": ["string", "null"]},
        "expiration":        {"type": ["string", "null"]},
        "dte":               {"type": ["integer", "null"], "minimum": 0},
        "short_call_strike": {"type": ["number", "null"]},
        "long_call_strike":  {"type": ["number", "null"]},
        "net_credit":        {"type": ["number", "null"]},
        "max_loss":          {"type": ["number", "null"]},
        "limit_price":       {"type": ["number", "null"]},
        "reasoning":         _REASONING_SCHEMA,
        "confidence":        {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason":       {"type": ["string", "null"]},
    },
}

IRON_CONDOR_IDLE = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "put_short_symbol", "put_long_symbol",
        "call_short_symbol", "call_long_symbol",
        "expiration", "dte",
        "total_credit", "max_loss", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":            {"type": "string", "enum": ["OPEN", "SKIP"]},
        "put_short_symbol":  {"type": ["string", "null"]},
        "put_long_symbol":   {"type": ["string", "null"]},
        "call_short_symbol": {"type": ["string", "null"]},
        "call_long_symbol":  {"type": ["string", "null"]},
        "expiration":        {"type": ["string", "null"]},
        "dte":               {"type": ["integer", "null"], "minimum": 0},
        "total_credit":      {"type": ["number", "null"]},
        "max_loss":          {"type": ["number", "null"]},
        "limit_price":       {"type": ["number", "null"]},
        "reasoning":         _REASONING_SCHEMA,
        "confidence":        {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason":       {"type": ["string", "null"]},
    },
}

IRON_BUTTERFLY_IDLE = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "put_long_symbol", "put_short_symbol",
        "call_short_symbol", "call_long_symbol",
        "expiration", "dte",
        "total_credit", "max_loss", "limit_price", "center_strike",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":            {"type": "string", "enum": ["OPEN", "SKIP"]},
        "put_long_symbol":   {"type": ["string", "null"]},
        "put_short_symbol":  {"type": ["string", "null"]},
        "call_short_symbol": {"type": ["string", "null"]},
        "call_long_symbol":  {"type": ["string", "null"]},
        "expiration":        {"type": ["string", "null"]},
        "dte":               {"type": ["integer", "null"], "minimum": 0},
        "total_credit":      {"type": ["number", "null"]},
        "max_loss":          {"type": ["number", "null"]},
        "limit_price":       {"type": ["number", "null"]},
        "center_strike":     {"type": ["number", "null"]},
        "reasoning":         _REASONING_SCHEMA,
        "confidence":        {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason":       {"type": ["string", "null"]},
    },
}

LONG_CALL_VERTICAL_IDLE = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "long_call_symbol", "short_call_symbol", "expiration",
        "dte", "long_call_strike", "short_call_strike",
        "net_debit", "max_gain", "break_even", "price_target", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":             {"type": "string", "enum": ["OPEN", "SKIP"]},
        "long_call_symbol":   {"type": ["string", "null"]},
        "short_call_symbol":  {"type": ["string", "null"]},
        "expiration":         {"type": ["string", "null"]},
        "dte":                {"type": ["integer", "null"], "minimum": 0},
        "long_call_strike":   {"type": ["number", "null"]},
        "short_call_strike":  {"type": ["number", "null"]},
        "net_debit":          {"type": ["number", "null"]},
        "max_gain":           {"type": ["number", "null"]},
        "break_even":         {"type": ["number", "null"]},
        "price_target":       {"type": ["number", "null"]},
        "limit_price":        {"type": ["number", "null"]},
        "reasoning":          _REASONING_SCHEMA,
        "confidence":         {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason":        {"type": ["string", "null"]},
    },
}

CALENDAR_SPREAD_IDLE = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "short_symbol", "long_symbol", "strike",
        "short_expiration", "long_expiration",
        "short_dte", "long_dte",
        "net_debit", "limit_price",
        "reasoning", "confidence", "skip_reason",
    ],
    "properties": {
        "action":            {"type": "string", "enum": ["OPEN", "SKIP"]},
        "short_symbol":      {"type": ["string", "null"]},
        "long_symbol":       {"type": ["string", "null"]},
        "strike":            {"type": ["number", "null"]},
        "short_expiration":  {"type": ["string", "null"]},
        "long_expiration":   {"type": ["string", "null"]},
        "short_dte":         {"type": ["integer", "null"], "minimum": 0},
        "long_dte":          {"type": ["integer", "null"], "minimum": 0},
        "net_debit":         {"type": ["number", "null"]},
        "limit_price":       {"type": ["number", "null"]},
        "reasoning":         _REASONING_SCHEMA,
        "confidence":        {"type": "string", "enum": ["high", "medium", "low"]},
        "skip_reason":       {"type": ["string", "null"]},
    },
}


# ── Spread open schemas (management decisions) ────────────────────────────────

_HOLD_CLOSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reasoning", "limit_price"],
    "properties": {
        "action":      {"type": "string", "enum": ["HOLD", "CLOSE"]},
        "reasoning":   _REASONING_SCHEMA,
        "limit_price": {"type": ["number", "null"]},
    },
}

_HOLD_CLOSE_WITH_URGENCY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reasoning", "urgency", "limit_price"],
    "properties": {
        "action":      {"type": "string", "enum": ["HOLD", "CLOSE"]},
        "reasoning":   _REASONING_SCHEMA,
        "urgency":     {"type": "string", "enum": ["immediate", "normal"]},
        "limit_price": {"type": ["number", "null"]},
    },
}

BULL_PUT_SPREAD_OPEN  = _HOLD_CLOSE_SCHEMA
LONG_CALL_VERTICAL_OPEN = _HOLD_CLOSE_SCHEMA

# Bear call and iron condor management include urgency for ex-div / breach signals
BEAR_CALL_SPREAD_OPEN = _HOLD_CLOSE_WITH_URGENCY_SCHEMA
IRON_CONDOR_OPEN      = _HOLD_CLOSE_WITH_URGENCY_SCHEMA
IRON_BUTTERFLY_OPEN   = _HOLD_CLOSE_WITH_URGENCY_SCHEMA

# Calendar spread has an additional ROLL_SHORT action
CALENDAR_SPREAD_OPEN = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reasoning", "limit_price"],
    "properties": {
        "action":      {"type": "string", "enum": ["HOLD", "CLOSE", "ROLL_SHORT"]},
        "reasoning":   _REASONING_SCHEMA,
        "limit_price": {"type": ["number", "null"]},
    },
}


# ── Schema registry ───────────────────────────────────────────────────────────

_REGISTRY: dict[str, dict] = {
    # Wheel
    "wheel_idle":        WHEEL_IDLE,
    "wheel_short_put":   WHEEL_SHORT_PUT,
    "wheel_long_stock":  WHEEL_LONG_STOCK,
    "wheel_short_call":  WHEEL_SHORT_CALL,
    # Bull put spread
    "bull_put_spread_idle": BULL_PUT_SPREAD_IDLE,
    "bull_put_spread_open": BULL_PUT_SPREAD_OPEN,
    # Bear call spread
    "bear_call_spread_idle": BEAR_CALL_SPREAD_IDLE,
    "bear_call_spread_open": BEAR_CALL_SPREAD_OPEN,
    # Iron condor
    "iron_condor_idle": IRON_CONDOR_IDLE,
    "iron_condor_open": IRON_CONDOR_OPEN,
    # Iron butterfly
    "iron_butterfly_idle": IRON_BUTTERFLY_IDLE,
    "iron_butterfly_open": IRON_BUTTERFLY_OPEN,
    # Long call vertical
    "long_call_vertical_idle": LONG_CALL_VERTICAL_IDLE,
    "long_call_vertical_open": LONG_CALL_VERTICAL_OPEN,
    # Calendar spread
    "calendar_spread_idle": CALENDAR_SPREAD_IDLE,
    "calendar_spread_open": CALENDAR_SPREAD_OPEN,
}


def get_schema(strategy: str, phase: str) -> dict:
    """Return the JSON schema dict for a given (strategy, phase) pair.

    Args:
        strategy: e.g. "wheel", "bull_put_spread", "iron_condor"
        phase: e.g. "idle", "short_put", "open"

    Returns:
        JSON schema dict suitable for output_config.format.schema.

    Raises:
        KeyError: if the (strategy, phase) combination is unknown.
    """
    key = f"{strategy}_{phase}"
    schema = _REGISTRY.get(key)
    if schema is None:
        raise KeyError(
            f"No structured output schema for '{key}'. "
            f"Available: {sorted(_REGISTRY)}"
        )
    return schema
