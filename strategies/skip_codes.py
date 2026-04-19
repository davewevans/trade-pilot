"""Canonical skip/rejection codes — single source of truth.

Every machine-readable reason code used for Claude skips AND guardrail
rejections is defined here. Use these constants everywhere; never create
ad-hoc code strings. The free-text ``skip_reason`` / ``rejection_reason``
fields remain for human readability; these codes enable reliable aggregation.
"""


class SkipCode:
    """String constants for all skip/rejection code values.

    Usage:
        from strategies.skip_codes import SkipCode

        # In a guardrail rejection:
        return False, "IV rank 22 < 30 minimum", SkipCode.LOW_IVR

        # In a journal entry:
        {"skip_code": SkipCode.EARNINGS_TOO_CLOSE, ...}
    """

    # ── Volatility / IV environment ────────────────────────────

    LOW_IVR = "LOW_IVR"
    """IV rank is below the minimum threshold for selling premium.
    Use when IVR < strategy-specific floor (e.g. 30 for CSP, 50 for iron condor)."""

    HIGH_IVR = "HIGH_IVR"
    """IV rank is too high for the strategy (e.g. debit spread entry in HIGH IV).
    Use for strategies that require LOW or MODERATE IV environments."""

    IV_ENV_MISMATCH = "IV_ENV_MISMATCH"
    """The iv_environment (LOW/MODERATE/HIGH) does not match the strategy requirement.
    Distinct from LOW_IVR: this code is for strategies that require a specific env label."""

    # ── Earnings / corporate events ────────────────────────────

    EARNINGS_TOO_CLOSE = "EARNINGS_TOO_CLOSE"
    """An earnings announcement falls within the forbidden window.
    Hard rule: no new entries within 21 days of earnings for most strategies."""

    EX_DIVIDEND_IN_WINDOW = "EX_DIVIDEND_IN_WINDOW"
    """Ex-dividend date falls within the option's DTE window.
    Primarily used for bear call spreads and covered calls (early assignment risk)."""

    # ── Market regime ──────────────────────────────────────────

    REGIME_MISMATCH = "REGIME_MISMATCH"
    """The confirmed market regime does not match the strategy's required regime.
    E.g. long call vertical requires BULL; iron condor requires NEUTRAL."""

    # ── Option selection / liquidity ───────────────────────────

    LIQUIDITY_INSUFFICIENT = "LIQUIDITY_INSUFFICIENT"
    """Open interest too low, bid-ask spread too wide, or no liquid contracts found.
    Use when options meet delta/DTE criteria but fail liquidity filters."""

    DELTA_OUT_OF_RANGE = "DELTA_OUT_OF_RANGE"
    """No contract with delta in the required range (e.g. -0.20 to -0.30 for CSP).
    Use when the chain has no qualifying strike at the current price level."""

    DTE_OUT_OF_RANGE = "DTE_OUT_OF_RANGE"
    """The selected DTE falls outside the allowed window for the strategy.
    Used by guardrails when the proposed contract's DTE is < min or > max."""

    NO_ELIGIBLE_STRIKE = "NO_ELIGIBLE_STRIKE"
    """The option chain has no strike satisfying all criteria simultaneously
    (delta, DTE, liquidity, credit). Use when the chain is exhausted without a match."""

    # ── Credit / debit sizing ──────────────────────────────────

    CREDIT_TOO_LOW = "CREDIT_TOO_LOW"
    """Net credit or total credit does not meet the minimum threshold.
    Also covers credit-to-width ratio below the floor."""

    DEBIT_TOO_HIGH = "DEBIT_TOO_HIGH"
    """Net debit exceeds the maximum allowed for debit spreads / calendars,
    OR falls outside the allowed debit range (too high or too low)."""

    # ── Macro event proximity ──────────────────────────────────

    MACRO_EVENT_PROXIMITY = "MACRO_EVENT_PROXIMITY"
    """A Tier 1 macro event (FOMC, CPI, NFP) is scheduled for the current or next
    trading day. New entries blocked until the session after the event."""

    # ── Position / account limits ──────────────────────────────

    POSITION_LIMIT_REACHED = "POSITION_LIMIT_REACHED"
    """An existing open position blocks this entry.
    Covers: duplicate CSP/CC on same root, sector concentration cap (3 per sector),
    duplicate open spread of same type, max concurrent wheel positions."""

    BUYING_POWER_INSUFFICIENT = "BUYING_POWER_INSUFFICIENT"
    """Position cost or max_loss exceeds the allowed percentage of buying power.
    Covers: per-trade hard cap, shared-account collective cap."""

    CIRCUIT_BREAKER_ACTIVE = "CIRCUIT_BREAKER_ACTIVE"
    """The circuit breaker has tripped (portfolio drawdown exceeded threshold).
    No new entries are permitted while the circuit breaker is active."""

    # ── Claude-initiated ───────────────────────────────────────

    CONFIDENCE_LOW = "CONFIDENCE_LOW"
    """Claude chose to skip because overall confidence is low.
    Use when no single hard filter triggered but the setup doesn't meet the bar."""

    STRIKE_BELOW_COST_BASIS = "STRIKE_BELOW_COST_BASIS"
    """Covered call: proposed strike is below the effective cost basis.
    Hard rule: never sell a CC below what you paid for the shares."""

    # ── Fallback ───────────────────────────────────────────────

    OTHER = "OTHER"
    """Catch-all for skip/rejection reasons not covered by the above codes.
    Also used for structural errors (invalid OCC symbol, wrong qty, etc.)
    and for legacy journal entries written before skip_code was introduced."""

    # ── All valid codes (for validation) ───────────────────────
    ALL: tuple[str, ...] = (
        LOW_IVR, HIGH_IVR, IV_ENV_MISMATCH,
        EARNINGS_TOO_CLOSE, EX_DIVIDEND_IN_WINDOW,
        REGIME_MISMATCH,
        LIQUIDITY_INSUFFICIENT, DELTA_OUT_OF_RANGE, DTE_OUT_OF_RANGE,
        NO_ELIGIBLE_STRIKE,
        CREDIT_TOO_LOW, DEBIT_TOO_HIGH,
        MACRO_EVENT_PROXIMITY,
        POSITION_LIMIT_REACHED, BUYING_POWER_INSUFFICIENT,
        CIRCUIT_BREAKER_ACTIVE,
        CONFIDENCE_LOW, STRIKE_BELOW_COST_BASIS,
        OTHER,
    )


def normalize_skip_code(code: str | None) -> str:
    """Return *code* if it is a known SkipCode value, else SkipCode.OTHER.

    Provides backward compat for journal entries written before skip_code
    was introduced — absent or unrecognised codes are bucketed as OTHER.
    """
    if code and code in SkipCode.ALL:
        return code
    return SkipCode.OTHER
