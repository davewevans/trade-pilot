"""Pre-execution guardrails that validate Claude's trade recommendations."""

import logging
import re
from datetime import date

from strategies.skip_codes import SkipCode

logger = logging.getLogger(__name__)

# ── Shared-account capital guard ───────────────────────────────
# The default Alpaca account is shared by three strategies:
# bull_put_spread, bear_call_spread, and long_call_vertical.
# These helpers prevent the three from collectively over-allocating
# even though each individually passes its own per-trade size check.

SHARED_ACCOUNT_STRATEGIES = frozenset({
    "bull_put_spread",
    "bear_call_spread",
    "long_call_vertical",
})


def get_committed_capital_on_shared_account(spread_tracker) -> float:
    """Return total max_loss committed on the shared account.

    Counts every non-terminal spread (PENDING_OPEN, OPEN, PENDING_CLOSE)
    so that capital is reserved the moment an entry order is submitted
    and is not released until the spread reaches CLOSED or CANCELED.
    """
    active_spreads = spread_tracker.get_active_spreads()
    return sum(
        float(s.get("max_loss", 0))
        for s in active_spreads
        if s.get("strategy_type") in SHARED_ACCOUNT_STRATEGIES
    )


def check_shared_account_buying_power(
    new_trade_max_loss: float,
    account_buying_power: float,
    spread_tracker,
    max_allocation_pct: float = 0.10,
) -> tuple[bool, str]:
    """Hard check for any new position on the shared (default) account.

    Rules:
    - Individual trade max_loss must not exceed 2% of buying_power.
    - Total committed capital (existing + new) must not exceed
      *max_allocation_pct* of buying_power.
    """
    if account_buying_power <= 0:
        return False, "Account buying power is zero or negative"

    individual_pct = new_trade_max_loss / account_buying_power
    if individual_pct > 0.02:
        return False, (
            f"Trade max_loss ${new_trade_max_loss:.2f} exceeds 2% of "
            f"buying power ${account_buying_power:.2f} "
            f"({individual_pct * 100:.1f}%)"
        )

    committed = get_committed_capital_on_shared_account(spread_tracker)
    total_after = committed + new_trade_max_loss
    total_pct = total_after / account_buying_power
    if total_pct > max_allocation_pct:
        return False, (
            f"Opening this trade would commit ${total_after:.2f} "
            f"({total_pct * 100:.1f}%) of the shared account's buying "
            f"power ${account_buying_power:.2f}. "
            f"Max allowed: {max_allocation_pct * 100:.0f}%. "
            f"Already committed: ${committed:.2f}"
        )

    return True, ""

_OCC_PUT_RE = re.compile(r"^[A-Z]+\d{6}P\d{8}$")
_OCC_CALL_RE = re.compile(r"^[A-Z]+\d{6}C\d{8}$")
_OCC_ROOT_RE = re.compile(r"^([A-Z]+)\d{6}[CP]\d{8}$")


class Guardrails:
    """Hard safety checks applied to every Claude recommendation before execution.

    These rules cannot be overridden by Claude's reasoning. If any check fails,
    the trade is rejected with a human-readable reason.
    """

    def __init__(self, broker=None, spread_tracker=None):
        # Optional broker reference used as a fallback to verify equity
        # ownership for covered-call checks when the positions list passed
        # to validate() does not include stock positions.
        self.broker = broker
        self.spread_tracker = spread_tracker

    def validate(
        self, decision: dict, account: dict, positions: list, context: dict | None = None
    ) -> tuple[bool, str]:
        """Validate a trade recommendation against hard safety rules.

        Args:
            decision: Claude's recommendation dict with keys: action, symbol,
                qty, order_type, limit_price, reason.
            account: Account info dict with buying_power, etc.
            positions: List of currently open position dicts.
            context: Optional full context dict (used for earnings check).

        Returns:
            Tuple of (is_valid, rejection_reason). If is_valid is True,
            rejection_reason is an empty string.
        """
        action = decision.get("action")

        # ── universal checks ─────────────────────────────────
        ok, reason = self._check_universal(decision)
        if not ok:
            return False, reason

        # ── action-specific checks ───────────────────────────
        if action == "sell_put":
            return self._check_sell_put(decision, account, positions, context)
        if action == "sell_call":
            return self._check_sell_call(decision, positions, context)
        if action == "roll":
            return self._check_roll(positions)
        if action in ("hold", "skip"):
            return True, ""

        return False, f"Unknown action: {action}"

    # ── universal ────────────────────────────────────────────

    @staticmethod
    def _check_universal(decision: dict) -> tuple[bool, str]:
        """Checks that apply to every actionable recommendation."""
        action = decision.get("action")

        if action in ("hold", "skip"):
            return True, ""

        qty = decision.get("qty")
        if not isinstance(qty, int) or qty <= 0:
            return False, f"qty must be a positive integer, got {qty!r}"

        if decision.get("order_type") == "limit":
            limit_price = decision.get("limit_price")
            if limit_price is None or limit_price <= 0:
                return False, f"limit_price must be > 0 for limit orders, got {limit_price!r}"

        return True, ""

    # ── csp entry (wheel) ─────────────────────────────────

    def validate_csp_entry(
        self,
        decision: dict,
        account: dict,
        positions: list,
        context: dict | None = None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Validate a CSP (cash-secured put) entry for the Wheel strategy.

        Reads thresholds from the wheel strategy definition's guardrails block.
        """
        if params is None:
            from strategies.strategy_loader import get_strategy_guardrail_params
            params = get_strategy_guardrail_params("wheel")
        return self._check_sell_put(decision, account, positions, context, params=params)

    def validate_cc_entry(
        self,
        decision: dict,
        positions: list,
        context: dict | None = None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Validate a covered call (CC) entry for the Wheel strategy.

        Reads thresholds from the wheel strategy definition's guardrails block.
        """
        if params is None:
            from strategies.strategy_loader import get_strategy_guardrail_params
            params = get_strategy_guardrail_params("wheel")
        return self._check_sell_call(decision, positions, context, params=params)

    # ── sell_put ─────────────────────────────────────────────

    # SYNC NOTE: The earnings threshold is enforced here in code
    # AND stated as a hard rule in prompts/system.md. If you change this
    # number, update both places.
    def _check_sell_put(
        self,
        decision: dict,
        account: dict,
        positions: list,
        context: dict | None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Validate a sell_put recommendation."""
        if params is None:
            params = {}

        symbol = decision.get("symbol") or ""

        if not _OCC_PUT_RE.match(symbol):
            return False, f"Invalid OCC put symbol: {symbol!r} (expected ROOT+YYMMDD+P+STRIKE)"

        if decision.get("qty") != 1:
            return False, f"qty must be 1 for a single wheel CSP, got {decision.get('qty')}"

        # Cost check: strike * 100 must be <= max_position_pct_of_bp_hard of buying power
        strike = self._extract_strike(symbol)
        # Prefer options_buying_power when surfaced by the broker; fall
        # back to plain buying_power for compatibility.
        buying_power = float(
            account.get("options_buying_power")
            or account.get("buying_power")
            or 0
        )
        cost = strike * 100
        max_bp_pct = params.get("max_position_pct_of_bp_hard", 10) / 100
        max_allowed = buying_power * max_bp_pct
        if cost > max_allowed:
            return (
                False,
                f"Position cost ${cost:,.0f} (strike {strike} × 100) exceeds "
                f"{max_bp_pct * 100:.0f}% of buying power ${max_allowed:,.0f}",
            )

        # No duplicate CSP on same root
        root = self._extract_root(symbol)
        for pos in positions:
            pos_sym = (pos.get("symbol") or "").upper()
            pos_root = self._extract_root(pos_sym)
            qty = float(pos.get("qty") or 0)
            if pos_root == root and "P" in pos_sym and qty < 0:
                return False, f"Already have an open short put on {root}: {pos_sym}"

        # Earnings proximity check
        earnings_block_days = params.get("earnings_hard_block_csp_days", 21)
        if context:
            earnings = context.get("earnings", {})
            days_until = earnings.get("days_until_earnings")
            if days_until is not None and days_until <= earnings_block_days:
                return (
                    False,
                    f"Earnings in {days_until} days — must be > {earnings_block_days} days away to sell a CSP",
                )

        # Sector concentration check: cap simultaneous short puts in one
        # sector so a single sector drawdown can't take out multiple wheels.
        from config import settings
        sector = settings.SYMBOL_SECTORS.get(root, "Unknown")
        if sector != "Unknown":
            same_sector_count = 0
            for pos in positions:
                pos_sym = (pos.get("symbol") or "").upper()
                pos_root = self._extract_root(pos_sym)
                pos_sector = settings.SYMBOL_SECTORS.get(pos_root, "")
                if (
                    pos_sector == sector
                    and "P" in pos_sym
                    and float(pos.get("qty") or 0) < 0
                ):
                    same_sector_count += 1
            if same_sector_count >= 3:
                return (
                    False,
                    f"Already have {same_sector_count} short-put positions in "
                    f"{sector} sector (max 3)",
                )

        return True, ""

    # ── sell_call ────────────────────────────────────────────

    def _check_sell_call(
        self,
        decision: dict,
        positions: list,
        context: dict | None = None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Validate a sell_call recommendation."""
        symbol = decision.get("symbol") or ""

        if not _OCC_CALL_RE.match(symbol):
            return False, f"Invalid OCC call symbol: {symbol!r} (expected ROOT+YYMMDD+C+STRIKE)"

        root = self._extract_root(symbol)

        # Must own >= 100 shares of the underlying. The positions list may
        # be options-only (legacy callers); if no equity row is present at
        # all, fall back to broker.get_equity_positions() to verify.
        def _owns_shares(pos_list) -> bool:
            for pos in pos_list:
                pos_sym = (pos.get("symbol") or "").upper()
                if pos_sym == root and float(pos.get("qty") or 0) >= 100:
                    return True
            return False

        has_shares = _owns_shares(positions)

        if not has_shares:
            has_any_equity = any(
                not _OCC_ROOT_RE.match((p.get("symbol") or "").upper())
                for p in positions
            )
            if not has_any_equity and self.broker is not None and hasattr(
                self.broker, "get_equity_positions"
            ):
                try:
                    has_shares = _owns_shares(self.broker.get_equity_positions())
                except Exception:
                    logger.warning(
                        "get_equity_positions fallback failed for %s",
                        root,
                        exc_info=True,
                    )

        if not has_shares:
            return False, f"Must own >= 100 shares of {root} to sell a covered call"

        # No duplicate CC on same root
        for pos in positions:
            pos_sym = (pos.get("symbol") or "").upper()
            pos_root = self._extract_root(pos_sym)
            qty = float(pos.get("qty") or 0)
            if pos_root == root and "C" in pos_sym and qty < 0:
                return False, f"Already have an open covered call on {root}: {pos_sym}"

        # Earnings proximity check
        earnings_block_days = (params or {}).get("earnings_hard_block_cc_days", 21)
        if context:
            earnings = context.get("earnings", {})
            days_until = earnings.get("days_until_earnings")
            if days_until is not None and days_until <= earnings_block_days:
                return (
                    False,
                    f"Earnings in {days_until} days — must be > {earnings_block_days} days away to sell a CC",
                )

            # Ex-dividend early-assignment soft warning. The actual risk
            # depends on moneyness at the ex-div date which we can't know
            # at entry, so this is a log-only warning — the prompt
            # instructs Claude to avoid ITM strikes near ex-div.
            ex_div = context.get("ex_dividend") or {}
            days_ex = ex_div.get("days_to_ex_dividend")
            div_yield = ex_div.get("annual_dividend_yield")
            if (
                days_ex is not None
                and div_yield is not None
                and div_yield > 0.01
            ):
                dte = None
                try:
                    from utils.occ import extract_expiration
                    exp = extract_expiration(symbol)
                    if exp:
                        dte = (exp - date.today()).days
                except Exception:
                    pass
                if dte is not None and days_ex <= dte:
                    logger.warning(
                        "CC on %s: ex-dividend in %d days within DTE %d "
                        "(annual yield %.1f%%) — early assignment risk",
                        root, days_ex, dte, div_yield * 100,
                    )

        return True, ""

    # ── roll ─────────────────────────────────────────────────

    @staticmethod
    def _check_roll(positions: list) -> tuple[bool, str]:
        """Validate a roll recommendation."""
        option_positions = [
            p for p in positions
            if float(p.get("qty") or 0) < 0  # short option positions
        ]
        if not option_positions:
            return False, "Cannot roll — no open short option position found"
        return True, ""

    # ── iron condor entry ──────────────────────────────────

    def validate_iron_condor_entry(
        self,
        decision: dict,
        context: dict,
        account: dict,
        open_condors: list | None = None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Hard rules for iron condor entry that cannot be overridden."""
        if params is None:
            from strategies.strategy_loader import get_strategy_guardrail_params
            params = get_strategy_guardrail_params("iron_condor")

        total_credit = decision.get("total_credit", 0)
        min_credit = params.get("min_total_credit_hard", 1.00)
        if total_credit <= min_credit:
            return False, f"Total credit ${total_credit} <= ${min_credit} minimum"

        max_loss = decision.get("max_loss", 0)
        buying_power = float(account.get("buying_power", 0))
        max_loss_pct = params.get("max_loss_hard_pct_of_bp", 5) / 100
        if buying_power > 0 and max_loss > buying_power * max_loss_pct:
            return (
                False,
                f"Max loss ${max_loss:,.0f} exceeds {max_loss_pct * 100:.0f}% of buying power "
                f"${buying_power * max_loss_pct:,.0f}",
            )

        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price >= 0:
            return False, f"limit_price must be negative for credit spread, got {limit_price}"

        dte = decision.get("dte")
        dte_min = params.get("dte_hard_min", 20)
        dte_max = params.get("dte_hard_max", 50)
        if dte is not None and (dte < dte_min or dte > dte_max):
            return False, f"DTE {dte} outside allowed range {dte_min}-{dte_max}"

        # Validate all 4 OCC symbols
        for key in ("put_short_symbol", "put_long_symbol",
                     "call_short_symbol", "call_long_symbol"):
            sym = decision.get(key, "")
            if not (_OCC_PUT_RE.match(sym) or _OCC_CALL_RE.match(sym)):
                return False, f"Invalid OCC symbol for {key}: {sym!r}"

        # Expiration consistency across all 4 legs
        from utils.occ import extract_expiration
        exps = set()
        for key in ("put_short_symbol", "put_long_symbol",
                     "call_short_symbol", "call_long_symbol"):
            exp = extract_expiration(decision.get(key, ""))
            if exp:
                exps.add(exp)
        if len(exps) > 1:
            return False, f"Iron condor legs have mismatched expirations: {exps}"

        # No duplicate condors on same underlying
        underlying = self._extract_root(decision.get("put_short_symbol", ""))
        if open_condors:
            for oc in open_condors:
                if oc.get("underlying", "").upper() == underlying.upper() and oc.get("status") not in ("closed", "canceled"):
                    return False, f"Already have an open iron condor on {underlying}"

        # Earnings check
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 30:
            return False, f"Earnings in {dte_earnings} days (hard block: need > 30)"

        # IV Rank minimum (code-level enforcement of documented IVR >= 50 rule)
        ivr = context.get("iv_rank") or (context.get("volatility") or {}).get("iv_rank_1y")
        if ivr is not None and ivr < 50:
            return False, f"IV rank {ivr} < 50 minimum for iron condor entry"

        return True, ""

    # ── iron butterfly entry ───────────────────────────────

    def validate_iron_butterfly_entry(
        self,
        decision: dict,
        context: dict,
        account: dict,
        open_butterflies: list | None = None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Hard rules for iron butterfly entry.

        Nearly identical to iron condor guardrails with one critical addition:
        the two short strikes must match (both ATM at the same center strike).
        """
        if params is None:
            from strategies.strategy_loader import get_strategy_guardrail_params
            params = get_strategy_guardrail_params("iron_butterfly")

        # limit_price must be negative (credit)
        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price >= 0:
            return False, f"limit_price must be negative for credit spread, got {limit_price}"

        # Total credit minimum
        total_credit = decision.get("total_credit", 0)
        min_credit = params.get("min_total_credit_hard", 1.00)
        if total_credit < min_credit:
            return False, f"Total credit ${total_credit} < ${min_credit} minimum"

        # Max loss check
        max_loss = decision.get("max_loss", 0)
        buying_power = float(account.get("buying_power", 0))
        max_loss_pct = params.get("max_loss_hard_pct_of_bp", 8)
        if buying_power > 0 and max_loss > buying_power * max_loss_pct / 100:
            return False, (
                f"Max loss ${max_loss:,.0f} exceeds {max_loss_pct}% of "
                f"buying power ${buying_power * max_loss_pct / 100:,.0f}"
            )

        # DTE range
        dte = decision.get("dte")
        dte_min = params.get("dte_hard_min", 15)
        dte_max = params.get("dte_hard_max", 50)
        if dte is not None and (dte < dte_min or dte > dte_max):
            return False, f"DTE {dte} outside allowed range {dte_min}-{dte_max}"

        # Validate all 4 OCC symbols
        for key in ("put_short_symbol", "put_long_symbol",
                     "call_short_symbol", "call_long_symbol"):
            sym = decision.get(key, "")
            if not (_OCC_PUT_RE.match(sym) or _OCC_CALL_RE.match(sym)):
                return False, f"Invalid OCC symbol for {key}: {sym!r}"

        # Expiration consistency across all 4 legs (same expiration, unlike calendar)
        from utils.occ import extract_expiration
        exps = set()
        for key in ("put_short_symbol", "put_long_symbol",
                     "call_short_symbol", "call_long_symbol"):
            exp = extract_expiration(decision.get(key, ""))
            if exp:
                exps.add(exp)
        if len(exps) > 1:
            return False, f"Iron butterfly legs have mismatched expirations: {exps}"

        # BUTTERFLY-SPECIFIC CHECK: short strikes must match (both ATM)
        from utils.occ import extract_strike
        put_short_strike = extract_strike(decision.get("put_short_symbol", ""))
        call_short_strike = extract_strike(decision.get("call_short_symbol", ""))
        if put_short_strike and call_short_strike and put_short_strike != call_short_strike:
            return False, (
                f"Iron butterfly requires matching short strikes: "
                f"put={put_short_strike} vs call={call_short_strike}"
            )

        # Credit-to-width ratio check
        center_strike = put_short_strike or call_short_strike
        put_long_strike = extract_strike(decision.get("put_long_symbol", ""))
        if center_strike and put_long_strike:
            wing_width = center_strike - put_long_strike
            if wing_width > 0:
                ratio = total_credit / wing_width
                min_ratio = params.get("min_credit_to_width_hard", 0.20)
                if ratio < min_ratio:
                    return False, (
                        f"Credit-to-width ratio {ratio:.2f} < {min_ratio} minimum "
                        f"(credit=${total_credit}, width=${wing_width})"
                    )

        # No duplicate butterflies on same underlying
        underlying = self._extract_root(decision.get("put_short_symbol", ""))
        if open_butterflies:
            for ob in open_butterflies:
                if (ob.get("underlying", "").upper() == underlying.upper()
                        and ob.get("status") not in ("closed", "canceled")):
                    return False, f"Already have an open iron butterfly on {underlying}"

        # Earnings check (30 days)
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 30:
            return False, f"Earnings in {dte_earnings} days (need > 30)"

        # IV Rank minimum
        ivr = context.get("iv_rank") or (context.get("volatility") or {}).get("iv_rank_1y")
        if ivr is not None and ivr < 50:
            return False, f"IV rank {ivr} < 50 minimum for iron butterfly"

        return True, ""

    # ── long call vertical entry ───────────────────────────

    def validate_long_call_vertical_entry(
        self,
        decision: dict,
        context: dict,
        account: dict,
        open_spreads: list | None = None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Hard rules for long call vertical (debit spread) entry."""
        if params is None:
            from strategies.strategy_loader import get_strategy_guardrail_params
            params = get_strategy_guardrail_params("long_call_vertical")

        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price <= 0:
            return False, f"limit_price must be positive for debit spread, got {limit_price}"

        net_debit = decision.get("net_debit", 0)
        min_debit = params.get("min_net_debit_hard", 0.20)
        max_debit = params.get("max_net_debit_hard", 2.00)
        if net_debit <= min_debit:
            return False, f"Net debit ${net_debit} <= ${min_debit} minimum"
        if net_debit >= max_debit:
            return False, f"Net debit ${net_debit} >= ${max_debit} maximum"

        # Max risk = debit * 100, must be < max_risk_hard_pct_of_bp of buying power
        max_risk = net_debit * 100
        buying_power = float(account.get("buying_power", 0))
        max_risk_pct = params.get("max_risk_hard_pct_of_bp", 1) / 100
        if buying_power > 0 and max_risk > buying_power * max_risk_pct:
            return (
                False,
                f"Max risk ${max_risk:,.0f} exceeds {max_risk_pct * 100:.0f}% of buying power "
                f"${buying_power * max_risk_pct:,.0f}",
            )

        dte = decision.get("dte")
        dte_min = params.get("dte_hard_min", 28)
        dte_max = params.get("dte_hard_max", 48)
        if dte is not None and (dte < dte_min or dte > dte_max):
            return False, f"DTE {dte} outside allowed range {dte_min}-{dte_max}"

        for key in ("long_call_symbol", "short_call_symbol"):
            sym = decision.get(key, "")
            if not _OCC_CALL_RE.match(sym):
                return False, f"Invalid OCC call symbol for {key}: {sym!r}"

        # Expiration consistency across legs
        from utils.occ import extract_expiration
        long_exp = extract_expiration(decision.get("long_call_symbol", ""))
        short_exp = extract_expiration(decision.get("short_call_symbol", ""))
        if long_exp and short_exp and long_exp != short_exp:
            return False, f"Leg expirations don't match: {long_exp} vs {short_exp}"

        # Regime must be BULL
        regime = context.get("confirmed_market_regime", "")
        if regime != "BULL":
            return False, f"Market regime must be BULL for debit spread, got {regime}"

        # IV must be LOW
        iv_env = context.get("iv_environment", "")
        if iv_env != "LOW":
            return False, f"IV environment must be LOW for debit spread, got {iv_env}"

        # No earnings within DTE
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte is not None and dte_earnings is not None and dte_earnings <= dte:
            return False, f"Earnings in {dte_earnings} days within DTE {dte}"

        underlying = self._extract_root(decision.get("long_call_symbol", ""))
        if open_spreads:
            for s in open_spreads:
                if (
                    s.get("underlying", "").upper() == underlying.upper()
                    and s.get("status") not in ("closed", "canceled")
                ):
                    return False, f"Already have an open long call vertical on {underlying}"

        return True, ""

    # ── bear call spread entry ─────────────────────────────

    def validate_bear_call_spread_entry(
        self,
        decision: dict,
        context: dict,
        account: dict,
        open_spreads: list | None = None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Hard rules for bear call spread entry."""
        if params is None:
            from strategies.strategy_loader import get_strategy_guardrail_params
            params = get_strategy_guardrail_params("bear_call_spread")

        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price >= 0:
            return False, f"limit_price must be negative for credit spread, got {limit_price}"

        net_credit = decision.get("net_credit", 0)
        min_credit = params.get("min_net_credit_hard", 0.50)
        if net_credit <= min_credit:
            return False, f"Net credit ${net_credit} <= ${min_credit} minimum"

        max_loss = decision.get("max_loss", 0)
        buying_power = float(account.get("buying_power", 0))
        max_loss_pct = params.get("max_loss_hard_pct_of_bp", 2) / 100
        if buying_power > 0 and max_loss > buying_power * max_loss_pct:
            return (
                False,
                f"Max loss ${max_loss:,.0f} exceeds {max_loss_pct * 100:.0f}% of buying power "
                f"${buying_power * max_loss_pct:,.0f}",
            )

        dte = decision.get("dte")
        dte_min = params.get("dte_hard_min", 20)
        dte_max = params.get("dte_hard_max", 45)
        if dte is not None and (dte < dte_min or dte > dte_max):
            return False, f"DTE {dte} outside allowed range {dte_min}-{dte_max}"

        for key in ("short_call_symbol", "long_call_symbol"):
            sym = decision.get(key, "")
            if not _OCC_CALL_RE.match(sym):
                return False, f"Invalid OCC call symbol for {key}: {sym!r}"

        # Expiration consistency across legs
        from utils.occ import extract_expiration
        short_exp = extract_expiration(decision.get("short_call_symbol", ""))
        long_exp = extract_expiration(decision.get("long_call_symbol", ""))
        if short_exp and long_exp and short_exp != long_exp:
            return False, f"Leg expirations don't match: {short_exp} vs {long_exp}"

        underlying = self._extract_root(decision.get("short_call_symbol", ""))
        if open_spreads:
            for s in open_spreads:
                if (
                    s.get("underlying", "").upper() == underlying.upper()
                    and s.get("status") not in ("closed", "canceled")
                ):
                    return False, f"Already have an open bear call spread on {underlying}"

        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 21:
            return False, f"Earnings in {dte_earnings} days (hard block: need > 21)"

        # Ex-dividend within DTE window
        days_ex = fund.get("days_to_ex_dividend")
        if days_ex is not None and dte is not None and days_ex <= dte:
            return False, f"Ex-dividend in {days_ex} days within DTE {dte} — early assignment risk"

        return True, ""

    # ── bull put spread entry ──────────────────────────────

    def validate_bull_put_spread_entry(
        self,
        decision: dict,
        context: dict,
        account: dict,
        open_spreads: list | None = None,
        params: dict | None = None,
    ) -> tuple[bool, str]:
        """Hard rules for bull put spread entry."""
        if params is None:
            from strategies.strategy_loader import get_strategy_guardrail_params
            params = get_strategy_guardrail_params("bull_put_spread")

        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price >= 0:
            return False, f"limit_price must be negative for credit spread, got {limit_price}"

        net_credit = decision.get("net_credit", 0)
        min_credit = params.get("min_net_credit_hard", 0.50)
        if net_credit <= min_credit:
            return False, f"Net credit ${net_credit} <= ${min_credit} minimum"

        max_loss = decision.get("max_loss", 0)
        buying_power = float(account.get("buying_power", 0))
        max_loss_pct = params.get("max_loss_hard_pct_of_bp", 2) / 100
        if buying_power > 0 and max_loss > buying_power * max_loss_pct:
            return (
                False,
                f"Max loss ${max_loss:,.0f} exceeds {max_loss_pct * 100:.0f}% of buying power "
                f"${buying_power * max_loss_pct:,.0f}",
            )

        dte = decision.get("dte")
        dte_min = params.get("dte_hard_min", 20)
        dte_max = params.get("dte_hard_max", 45)
        if dte is not None and (dte < dte_min or dte > dte_max):
            return False, f"DTE {dte} outside allowed range {dte_min}-{dte_max}"

        for key in ("short_put_symbol", "long_put_symbol"):
            sym = decision.get(key, "")
            if not _OCC_PUT_RE.match(sym):
                return False, f"Invalid OCC put symbol for {key}: {sym!r}"

        # Expiration consistency across legs
        from utils.occ import extract_expiration
        short_exp = extract_expiration(decision.get("short_put_symbol", ""))
        long_exp = extract_expiration(decision.get("long_put_symbol", ""))
        if short_exp and long_exp and short_exp != long_exp:
            return False, f"Leg expirations don't match: {short_exp} vs {long_exp}"

        # No duplicate on same underlying
        underlying = self._extract_root(decision.get("short_put_symbol", ""))
        if open_spreads:
            for s in open_spreads:
                if (
                    s.get("underlying", "").upper() == underlying.upper()
                    and s.get("status") not in ("closed", "canceled")
                ):
                    return False, f"Already have an open bull put spread on {underlying}"

        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 21:
            return False, f"Earnings in {dte_earnings} days (hard block: need > 21)"

        return True, ""

    # ── calendar spread entry ──────────────────────────────

    def validate_calendar_spread_entry(
        self,
        decision: dict,
        context: dict,
        account: dict,
        open_spreads: list | None = None,
    ) -> tuple[bool, str]:
        """Hard rules for calendar spread entry."""
        short_symbol = decision.get("short_symbol", "")
        long_symbol = decision.get("long_symbol", "")

        # Both symbols must be same type (both calls or both puts)
        is_short_put = _OCC_PUT_RE.match(short_symbol)
        is_short_call = _OCC_CALL_RE.match(short_symbol)
        is_long_put = _OCC_PUT_RE.match(long_symbol)
        is_long_call = _OCC_CALL_RE.match(long_symbol)

        if not (is_short_put or is_short_call):
            return False, f"Invalid OCC symbol for short_symbol: {short_symbol!r}"
        if not (is_long_put or is_long_call):
            return False, f"Invalid OCC symbol for long_symbol: {long_symbol!r}"

        # Same type check
        short_is_put = bool(is_short_put)
        long_is_put = bool(is_long_put)
        if short_is_put != long_is_put:
            return False, "Both legs must be the same option type (both calls or both puts)"

        # Same strike
        from utils.occ import extract_strike, extract_expiration
        short_strike = extract_strike(short_symbol)
        long_strike = extract_strike(long_symbol)
        if short_strike and long_strike and abs(short_strike - long_strike) > 0.01:
            return False, f"Calendar legs must have same strike: {short_strike} vs {long_strike}"

        # Short expiration must be before long expiration
        short_exp = extract_expiration(short_symbol)
        long_exp = extract_expiration(long_symbol)
        if short_exp and long_exp:
            if short_exp >= long_exp:
                return (
                    False,
                    f"Short leg expiration {short_exp} must be before long leg {long_exp}",
                )

        # limit_price must be positive (debit)
        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price <= 0:
            return False, f"limit_price must be positive for debit calendar, got {limit_price}"

        # Net debit range $0.50 – $2.50
        net_debit = decision.get("net_debit", 0)
        if not (0.50 <= net_debit <= 2.50):
            return False, f"Net debit ${net_debit} outside allowed range $0.50–$2.50"

        # Net debit <= 1% of buying power
        buying_power = float(account.get("buying_power", 0))
        if buying_power > 0 and (net_debit * 100) > buying_power * 0.01:
            return (
                False,
                f"Net debit ${net_debit * 100:,.0f} exceeds 1% of buying power "
                f"${buying_power * 0.01:,.0f}",
            )

        # Earnings within 21 days — hard block
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 21:
            return False, f"Earnings in {dte_earnings} days (hard block)"

        # One open calendar per underlying at a time
        underlying = self._extract_root(short_symbol)
        if open_spreads:
            for s in open_spreads:
                if (
                    s.get("underlying", "").upper() == underlying.upper()
                    and s.get("status") not in ("closed", "canceled")
                ):
                    return False, f"Already have an open calendar spread on {underlying}"

        return True, ""

    def validate_calendar_spread_exit(
        self,
        decision: dict,
        context: dict,
    ) -> tuple[bool, str]:
        """Validate a calendar spread exit decision."""
        spread_id = decision.get("spread_id")
        if not spread_id:
            return False, "Missing spread_id for calendar exit"
        return True, ""

    # ── skip-code classification ─────────────────────────────

    @staticmethod
    def classify_rejection(rejection_reason: str) -> str:
        """Map a free-text rejection reason to a canonical SkipCode.

        Used by callers that write guardrail rejections to the journal so
        the entry carries a machine-aggregatable skip_code alongside the
        human-readable rejection_reason string.

        Keyword matching is intentionally conservative: when no pattern
        matches, SkipCode.OTHER is returned rather than guessing.
        """
        if not rejection_reason:
            return SkipCode.OTHER
        r = rejection_reason.lower()

        # Earnings / events
        if "earnings" in r:
            return SkipCode.EARNINGS_TOO_CLOSE
        if "ex-dividend" in r or "ex_dividend" in r:
            return SkipCode.EX_DIVIDEND_IN_WINDOW

        # Volatility / IV
        if "iv rank" in r and any(w in r for w in ("minimum", "<", "< 50", "< 30")):
            return SkipCode.LOW_IVR
        if "iv environment" in r:
            return SkipCode.IV_ENV_MISMATCH

        # Market regime
        if "regime must be" in r or ("regime" in r and "mismatch" in r):
            return SkipCode.REGIME_MISMATCH

        # Buying power / capital
        if "buying power" in r or "committed" in r and "capital" in r:
            return SkipCode.BUYING_POWER_INSUFFICIENT

        # Position limits
        if "already have an open" in r or (
            "sector" in r and any(w in r for w in ("max", "positions"))
        ):
            return SkipCode.POSITION_LIMIT_REACHED

        # DTE
        if "dte" in r and "outside" in r:
            return SkipCode.DTE_OUT_OF_RANGE

        # Credit / debit limits
        if (
            ("net credit" in r or "total credit" in r)
            and any(w in r for w in ("minimum", "<=", "<", "below"))
        ):
            return SkipCode.CREDIT_TOO_LOW
        if "credit-to-width" in r and any(w in r for w in ("minimum", "<")):
            return SkipCode.CREDIT_TOO_LOW
        if "net debit" in r and any(w in r for w in ("maximum", ">=", "outside")):
            return SkipCode.DEBIT_TOO_HIGH

        # Circuit breaker (message generated in circuit_breaker.py, not here)
        if "circuit breaker" in r:
            return SkipCode.CIRCUIT_BREAKER_ACTIVE

        return SkipCode.OTHER

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _extract_strike(occ_symbol: str) -> float:
        """Extract the strike price from an OCC symbol."""
        from utils.occ import extract_strike
        return extract_strike(occ_symbol) or 0.0

    @staticmethod
    def _extract_root(occ_symbol: str) -> str:
        """Extract the root ticker from an OCC symbol."""
        from utils.occ import extract_root
        return extract_root(occ_symbol) or ""
