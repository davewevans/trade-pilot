"""Pre-execution guardrails that validate Claude's trade recommendations."""

import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

_OCC_PUT_RE = re.compile(r"^[A-Z]+\d{6}P\d{8}$")
_OCC_CALL_RE = re.compile(r"^[A-Z]+\d{6}C\d{8}$")
_OCC_ROOT_RE = re.compile(r"^([A-Z]+)\d{6}[CP]\d{8}$")


class Guardrails:
    """Hard safety checks applied to every Claude recommendation before execution.

    These rules cannot be overridden by Claude's reasoning. If any check fails,
    the trade is rejected with a human-readable reason.
    """

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
            return self._check_sell_call(decision, positions)
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
        buying_power = float(account.get("buying_power") or 0)
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
            if days_until is not None and days_until <= 14:
                return (
                    False,
                    f"Earnings in {days_until} days — must be > 14 days away to sell a CSP",
                )

        return True, ""

    # ── sell_call ────────────────────────────────────────────

    def _check_sell_call(
        self, decision: dict, positions: list
    ) -> tuple[bool, str]:
        """Validate a sell_call recommendation."""
        symbol = decision.get("symbol") or ""

        if not _OCC_CALL_RE.match(symbol):
            return False, f"Invalid OCC call symbol: {symbol!r} (expected ROOT+YYMMDD+C+STRIKE)"

        root = self._extract_root(symbol)

        # Must own >= 100 shares of the underlying
        has_shares = False
        for pos in positions:
            pos_sym = (pos.get("symbol") or "").upper()
            if pos_sym == root:
                qty = float(pos.get("qty") or 0)
                if qty >= 100:
                    has_shares = True
                    break

        if not has_shares:
            return False, f"Must own >= 100 shares of {root} to sell a covered call"

        # No duplicate CC on same root
        for pos in positions:
            pos_sym = (pos.get("symbol") or "").upper()
            pos_root = self._extract_root(pos_sym)
            qty = float(pos.get("qty") or 0)
            if pos_root == root and "C" in pos_sym and qty < 0:
                return False, f"Already have an open covered call on {root}: {pos_sym}"

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
        if total_credit <= 0.50:
            return False, f"Total credit ${total_credit} <= $0.50 minimum"

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

        # No duplicate condors on same underlying
        underlying = self._extract_root(decision.get("put_short_symbol", ""))
        if open_condors:
            for oc in open_condors:
                if oc.get("underlying", "").upper() == underlying.upper() and oc.get("status") == "open":
                    return False, f"Already have an open iron condor on {underlying}"

        # Earnings check
        fund = context.get("fundamentals") or {}
        dte_earnings = fund.get("days_to_earnings")
        if dte_earnings is not None and dte_earnings <= 30:
            return False, f"Earnings in {dte_earnings} days (hard block: need > 30)"

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
        if net_credit <= 0.25:
            return False, f"Net credit ${net_credit} <= $0.25 minimum"

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

        # No duplicate on same underlying
        underlying = self._extract_root(decision.get("short_put_symbol", ""))
        if open_spreads:
            for s in open_spreads:
                if (
                    s.get("underlying", "").upper() == underlying.upper()
                    and s.get("status") == "open"
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
        """Extract the strike price from an OCC symbol.

        The last 8 digits represent strike * 1000 (e.g. 00540000 = $540.00).
        """
        return int(occ_symbol[-8:]) / 1000

    @staticmethod
    def _extract_root(occ_symbol: str) -> str:
        """Extract the root ticker from an OCC symbol (e.g. 'SPY' from 'SPY260417P00540000')."""
        match = _OCC_ROOT_RE.match(occ_symbol.upper())
        return match.group(1) if match else ""
