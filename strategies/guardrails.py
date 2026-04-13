"""Pre-execution guardrails that validate Claude's trade recommendations."""

import logging
import re
from datetime import date

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

    def __init__(self, broker=None):
        # Optional broker reference used as a fallback to verify equity
        # ownership for covered-call checks when the positions list passed
        # to validate() does not include stock positions.
        self.broker = broker

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

    # ── sell_put ─────────────────────────────────────────────

    # SYNC NOTE: The 21-day earnings threshold is enforced here in code
    # AND stated as a hard rule in prompts/system.md. If you change this
    # number, update both places. The prompt says "no earnings within 21
    # days" — this guardrail is the code-level enforcement of that rule.
    def _check_sell_put(
        self, decision: dict, account: dict, positions: list, context: dict | None
    ) -> tuple[bool, str]:
        """Validate a sell_put recommendation."""
        symbol = decision.get("symbol") or ""

        if not _OCC_PUT_RE.match(symbol):
            return False, f"Invalid OCC put symbol: {symbol!r} (expected ROOT+YYMMDD+P+STRIKE)"

        if decision.get("qty") != 1:
            return False, f"qty must be 1 for a single wheel CSP, got {decision.get('qty')}"

        # Cost check: strike * 100 must be <= 10% of buying power
        strike = self._extract_strike(symbol)
        # Prefer options_buying_power when surfaced by the broker; fall
        # back to plain buying_power for compatibility.
        buying_power = float(
            account.get("options_buying_power")
            or account.get("buying_power")
            or 0
        )
        cost = strike * 100
        max_allowed = buying_power * 0.10
        if cost > max_allowed:
            return (
                False,
                f"Position cost ${cost:,.0f} (strike {strike} × 100) exceeds "
                f"10% of buying power ${max_allowed:,.0f}",
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
        if context:
            earnings = context.get("earnings", {})
            days_until = earnings.get("days_until_earnings")
            if days_until is not None and days_until <= 21:
                return (
                    False,
                    f"Earnings in {days_until} days — must be > 21 days away to sell a CSP",
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
        self, decision: dict, positions: list, context: dict | None = None
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
        if context:
            earnings = context.get("earnings", {})
            days_until = earnings.get("days_until_earnings")
            if days_until is not None and days_until <= 21:
                return (
                    False,
                    f"Earnings in {days_until} days — must be > 21 days away to sell a CC",
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
    ) -> tuple[bool, str]:
        """Hard rules for iron condor entry that cannot be overridden."""
        total_credit = decision.get("total_credit", 0)
        if total_credit <= 1.00:
            return False, f"Total credit ${total_credit} <= $1.00 minimum"

        max_loss = decision.get("max_loss", 0)
        buying_power = float(account.get("buying_power", 0))
        if buying_power > 0 and max_loss > buying_power * 0.05:
            return (
                False,
                f"Max loss ${max_loss:,.0f} exceeds 5% of buying power "
                f"${buying_power * 0.05:,.0f}",
            )

        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price >= 0:
            return False, f"limit_price must be negative for credit spread, got {limit_price}"

        dte = decision.get("dte")
        if dte is not None and (dte < 20 or dte > 50):
            return False, f"DTE {dte} outside allowed range 20-50"

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

    # ── long call vertical entry ───────────────────────────

    def validate_long_call_vertical_entry(
        self,
        decision: dict,
        context: dict,
        account: dict,
        open_spreads: list | None = None,
    ) -> tuple[bool, str]:
        """Hard rules for long call vertical (debit spread) entry."""
        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price <= 0:
            return False, f"limit_price must be positive for debit spread, got {limit_price}"

        net_debit = decision.get("net_debit", 0)
        if net_debit <= 0.20:
            return False, f"Net debit ${net_debit} <= $0.20 minimum"
        if net_debit >= 2.00:
            return False, f"Net debit ${net_debit} >= $2.00 maximum"

        # Max risk = debit * 100, must be < 1% of buying power
        max_risk = net_debit * 100
        buying_power = float(account.get("buying_power", 0))
        if buying_power > 0 and max_risk > buying_power * 0.01:
            return (
                False,
                f"Max risk ${max_risk:,.0f} exceeds 1% of buying power "
                f"${buying_power * 0.01:,.0f}",
            )

        dte = decision.get("dte")
        if dte is not None and (dte < 28 or dte > 48):
            return False, f"DTE {dte} outside allowed range 28-48"

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
    ) -> tuple[bool, str]:
        """Hard rules for bear call spread entry."""
        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price >= 0:
            return False, f"limit_price must be negative for credit spread, got {limit_price}"

        net_credit = decision.get("net_credit", 0)
        if net_credit <= 0.50:
            return False, f"Net credit ${net_credit} <= $0.50 minimum"

        max_loss = decision.get("max_loss", 0)
        buying_power = float(account.get("buying_power", 0))
        if buying_power > 0 and max_loss > buying_power * 0.02:
            return (
                False,
                f"Max loss ${max_loss:,.0f} exceeds 2% of buying power "
                f"${buying_power * 0.02:,.0f}",
            )

        dte = decision.get("dte")
        if dte is not None and (dte < 20 or dte > 45):
            return False, f"DTE {dte} outside allowed range 20-45"

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
    ) -> tuple[bool, str]:
        """Hard rules for bull put spread entry."""
        limit_price = decision.get("limit_price")
        if limit_price is not None and limit_price >= 0:
            return False, f"limit_price must be negative for credit spread, got {limit_price}"

        net_credit = decision.get("net_credit", 0)
        if net_credit <= 0.50:
            return False, f"Net credit ${net_credit} <= $0.50 minimum"

        max_loss = decision.get("max_loss", 0)
        buying_power = float(account.get("buying_power", 0))
        if buying_power > 0 and max_loss > buying_power * 0.02:
            return (
                False,
                f"Max loss ${max_loss:,.0f} exceeds 2% of buying power "
                f"${buying_power * 0.02:,.0f}",
            )

        dte = decision.get("dte")
        if dte is not None and (dte < 20 or dte > 45):
            return False, f"DTE {dte} outside allowed range 20-45"

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
