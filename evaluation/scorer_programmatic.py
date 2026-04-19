"""Programmatic (non-LLM) scorer for trade decisions.

Implements the rule_adherence and skip_validity_structural dimensions
described in knowledge/synthesized/decision_rubric.md §5.1 and §5.3.

Boundary convention (§5.1):
  Values exactly at a threshold are treated as PASS, not fail.
  e.g. delta = 0.20 with delta_min = 0.20 → pass
       DTE = 21 with dte_min = 21 → pass
  This applies consistently to all ≥/≤ threshold comparisons.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from strategies.skip_codes import SkipCode
from strategies.strategy_loader import get_strategy_entry_params

logger = logging.getLogger(__name__)

# Actions that represent new position entries (rule_adherence applies).
_ENTRY_ACTIONS = frozenset({"SELL_PUT", "SELL_CALL", "OPEN"})

# Actions for which no programmatic dimension applies.
_UNSCORED_ACTIONS = frozenset({"HOLD", "CLOSE", "ROLL"})

# Default earnings consistency window when strategy params are unavailable.
_DEFAULT_EARNINGS_BUFFER_DAYS = 21

# IVR threshold above which we consider IV "high" for HIGH_IVR consistency checks.
_HIGH_IVR_THRESHOLD = 70


class ProgrammaticScorer:
    """Score individual decisions against the documented strategy rules.

    Usage::

        scorer = ProgrammaticScorer()
        dimension_dicts = scorer.score_decision(decision_row)

    Each element of the returned list corresponds to one scored dimension.
    Dimensions that do not apply to the decision's action are omitted.
    """

    SCORER_TYPE = "programmatic"

    def score_decision(self, decision: dict) -> list[dict]:
        """Score a single decision record.

        Args:
            decision: A decision row dict as returned by DecisionRepository
                (i.e. context_json already decoded into ``context``,
                reasoning already parsed if it was valid JSON).

        Returns:
            List of dimension-score dicts, one per applicable dimension.
            Each dict has keys: decision_id, dimension, score (float 0–1),
            score_metadata (dict).  Returns an empty list when no dimension
            applies (e.g. HOLD, CLOSE, ROLL).
        """
        action = (decision.get("action") or "").upper()
        results: list[dict] = []

        if action in _ENTRY_ACTIONS:
            results.append(self._score_rule_adherence(decision))
        elif action == "SKIP":
            results.append(self._score_skip_validity_structural(decision))
        # HOLD, CLOSE, ROLL → no programmatic dimension scored

        return results

    # ── rule_adherence ────────────────────────────────────────────────────────

    def _score_rule_adherence(self, decision: dict) -> dict:
        """Score rule adherence for an entry decision.

        Checks each strategy rule that has observable data available.
        Rules with missing data are skipped (excluded from denominator).
        Score = rules_passed / rules_checked.
        """
        decision_id = decision.get("id")
        strategy_type = decision.get("strategy_type") or ""
        action = (decision.get("action") or "").upper()
        context = decision.get("context") or {}
        reasoning = _parse_reasoning(decision.get("reasoning"))

        details: list[dict] = []
        skipped_rules: list[str] = []

        # Dispatch to strategy-specific rule set
        if strategy_type in ("wheel", "turnover_wheel"):
            if action == "SELL_PUT":
                details, skipped_rules = self._check_wheel_csp_rules(
                    strategy_type, context, reasoning
                )
            elif action == "SELL_CALL":
                details, skipped_rules = self._check_wheel_cc_rules(
                    strategy_type, context, reasoning
                )
        elif strategy_type == "bull_put_spread":
            details, skipped_rules = self._check_credit_spread_rules(
                strategy_type, context, reasoning, spread_type="bull_put"
            )
        elif strategy_type == "bear_call_spread":
            details, skipped_rules = self._check_credit_spread_rules(
                strategy_type, context, reasoning, spread_type="bear_call"
            )
        elif strategy_type == "long_call_vertical":
            details, skipped_rules = self._check_long_call_vertical_rules(
                context, reasoning
            )
        elif strategy_type in ("iron_condor", "iron_butterfly"):
            details, skipped_rules = self._check_iron_strategy_rules(
                strategy_type, context, reasoning
            )
        elif strategy_type == "calendar_spread":
            details, skipped_rules = self._check_calendar_spread_rules(
                context, reasoning
            )

        rules_checked = len(details)
        rules_passed = sum(1 for d in details if d["passed"])
        score = rules_passed / rules_checked if rules_checked > 0 else 1.0

        return {
            "decision_id": decision_id,
            "dimension": "rule_adherence",
            "score": score,
            "score_metadata": {
                "rules_checked": rules_checked,
                "rules_passed": rules_passed,
                "details": details,
                "skipped_rules": skipped_rules,
            },
        }

    def _check_wheel_csp_rules(
        self, strategy_type: str, context: dict, reasoning: dict
    ) -> tuple[list[dict], list[str]]:
        try:
            params = get_strategy_entry_params(strategy_type).get("csp", {})
        except Exception:
            params = {}

        details: list[dict] = []
        skipped: list[str] = []

        # IVR floor — boundary: exactly at minimum is a pass
        iv_rank = _get_iv_rank(context)
        iv_rank_min = params.get("iv_rank_min", 30)
        if iv_rank is not None:
            details.append(_rule("ivr_floor", iv_rank >= iv_rank_min, iv_rank, f">={iv_rank_min}"))
        else:
            skipped.append("ivr_floor")

        # Earnings buffer — boundary: exactly at limit is a pass (> handled as >=)
        earnings_days = _get_earnings_days(context)
        earnings_buffer = params.get("earnings_buffer_days", 21)
        if earnings_days is not None:
            details.append(
                _rule("earnings_buffer", earnings_days > earnings_buffer, earnings_days, f">{earnings_buffer}")
            )
        else:
            skipped.append("earnings_buffer")

        # Delta range — boundary: exactly at edge is a pass
        delta = _get_delta(reasoning, context)
        if delta is not None:
            delta_min = params.get("delta_min", 0.20)
            delta_max = params.get("delta_max", 0.30)
            details.append(
                _rule("delta_range", delta_min <= delta <= delta_max, delta, f"{delta_min}–{delta_max}")
            )
        else:
            skipped.append("delta_range")

        # DTE range — boundary: exactly at edge is a pass
        dte = _get_dte(reasoning, context)
        if dte is not None:
            dte_min = params.get("dte_min", 21)
            dte_max = params.get("dte_max", 35)
            details.append(
                _rule("dte_range", dte_min <= dte <= dte_max, dte, f"{dte_min}–{dte_max}")
            )
        else:
            skipped.append("dte_range")

        # Open interest / liquidity
        oi = _get_open_interest(reasoning, context)
        oi_min = params.get("min_open_interest", 200)
        if oi is not None:
            details.append(_rule("open_interest", oi >= oi_min, oi, f">={oi_min}"))
        else:
            skipped.append("open_interest")

        return details, skipped

    def _check_wheel_cc_rules(
        self, strategy_type: str, context: dict, reasoning: dict
    ) -> tuple[list[dict], list[str]]:
        try:
            params = get_strategy_entry_params(strategy_type).get("cc", {})
        except Exception:
            params = {}

        details: list[dict] = []
        skipped: list[str] = []

        # Earnings buffer
        earnings_days = _get_earnings_days(context)
        earnings_buffer = params.get("earnings_buffer_days", 21)
        if earnings_days is not None:
            details.append(
                _rule("earnings_buffer", earnings_days > earnings_buffer, earnings_days, f">{earnings_buffer}")
            )
        else:
            skipped.append("earnings_buffer")

        # Delta range — only scored when the strategy defines CC delta bounds.
        # turnover_wheel has no delta cap on CC entry (cost-basis only), so
        # delta_min/delta_max are absent from its JSON; skip the rule entirely.
        if "delta_min" in params and "delta_max" in params:
            delta = _get_delta(reasoning, context)
            if delta is not None:
                delta_min = params["delta_min"]
                delta_max = params["delta_max"]
                details.append(
                    _rule("delta_range", delta_min <= delta <= delta_max, delta, f"{delta_min}–{delta_max}")
                )
            else:
                skipped.append("delta_range")
        else:
            # No delta bounds defined — no delta filter applied for this strategy's CC.
            skipped.append("delta_range")

        # DTE range
        dte = _get_dte(reasoning, context)
        if dte is not None:
            dte_min = params.get("dte_min", 21)
            dte_max = params.get("dte_max", 35)
            details.append(
                _rule("dte_range", dte_min <= dte <= dte_max, dte, f"{dte_min}–{dte_max}")
            )
        else:
            skipped.append("dte_range")

        # Strike above cost basis — turnover_wheel requires this
        if params.get("strike_above_cost_basis", False):
            cost_basis = context.get("cost_basis") or context.get("position", {}).get("cost_basis")
            strike = reasoning.get("strike")
            if cost_basis is not None and strike is not None:
                details.append(
                    _rule("strike_above_cost_basis", float(strike) >= float(cost_basis), strike, f">={cost_basis}")
                )
            else:
                skipped.append("strike_above_cost_basis")

        return details, skipped

    def _check_credit_spread_rules(
        self,
        strategy_type: str,
        context: dict,
        reasoning: dict,
        spread_type: str,
    ) -> tuple[list[dict], list[str]]:
        try:
            params = get_strategy_entry_params(strategy_type)
        except Exception:
            params = {}

        details: list[dict] = []
        skipped: list[str] = []

        # Earnings buffer
        earnings_days = _get_earnings_days(context)
        earnings_buffer = params.get("earnings_buffer_days", 21)
        if earnings_days is not None:
            details.append(
                _rule("earnings_buffer", earnings_days > earnings_buffer, earnings_days, f">{earnings_buffer}")
            )
        else:
            skipped.append("earnings_buffer")

        # IV environment
        iv_env = context.get("iv_environment")
        allowed_iv_envs: list[str] = []
        try:
            from strategies.strategy_loader import load_strategy
            strat = load_strategy(strategy_type)
            allowed_iv_envs = strat.get("iv_environment", {}).get("allowed", [])
        except Exception:
            pass
        if iv_env is not None and allowed_iv_envs:
            details.append(
                _rule("iv_environment", iv_env in allowed_iv_envs, iv_env, f"in {allowed_iv_envs}")
            )
        else:
            skipped.append("iv_environment")

        # Delta range
        delta = _get_delta(reasoning, context, keys=("short_delta", "delta"))
        if delta is not None:
            delta_min = params.get("short_delta_min", 0.20)
            delta_max = params.get("short_delta_max", 0.30)
            details.append(
                _rule("delta_range", delta_min <= delta <= delta_max, delta, f"{delta_min}–{delta_max}")
            )
        else:
            skipped.append("delta_range")

        # DTE range
        dte = _get_dte(reasoning, context)
        if dte is not None:
            dte_min = params.get("dte_min", 21)
            dte_max = params.get("dte_max", 35)
            details.append(
                _rule("dte_range", dte_min <= dte <= dte_max, dte, f"{dte_min}–{dte_max}")
            )
        else:
            skipped.append("dte_range")

        # Net credit floor
        net_credit = reasoning.get("net_credit")
        if net_credit is not None:
            min_credit = params.get("min_net_credit", 0.50)
            details.append(
                _rule("net_credit_floor", float(net_credit) >= min_credit, net_credit, f">={min_credit}")
            )
        else:
            skipped.append("net_credit_floor")

        return details, skipped

    def _check_long_call_vertical_rules(
        self, context: dict, reasoning: dict
    ) -> tuple[list[dict], list[str]]:
        try:
            params = get_strategy_entry_params("long_call_vertical")
        except Exception:
            params = {}

        details: list[dict] = []
        skipped: list[str] = []

        # Market regime must be BULL
        regime = context.get("confirmed_market_regime")
        if regime is not None:
            details.append(_rule("regime_bull", regime == "BULL", regime, "BULL"))
        else:
            skipped.append("regime_bull")

        # IV environment must be LOW
        iv_env = context.get("iv_environment")
        if iv_env is not None:
            details.append(_rule("iv_environment_low", iv_env == "LOW", iv_env, "LOW"))
        else:
            skipped.append("iv_environment_low")

        # DTE range
        dte = _get_dte(reasoning, context)
        if dte is not None:
            dte_min = params.get("dte_min", 30)
            dte_max = params.get("dte_max", 60)
            details.append(
                _rule("dte_range", dte_min <= dte <= dte_max, dte, f"{dte_min}–{dte_max}")
            )
        else:
            skipped.append("dte_range")

        # Net debit in range
        net_debit = reasoning.get("net_debit")
        if net_debit is not None:
            min_debit = params.get("min_net_debit", 0.20)
            max_debit = params.get("max_net_debit", 2.00)
            passed = min_debit <= float(net_debit) <= max_debit
            details.append(_rule("net_debit_range", passed, net_debit, f"{min_debit}–{max_debit}"))
        else:
            skipped.append("net_debit_range")

        # Earnings must clear expiry
        earnings_days = _get_earnings_days(context)
        if earnings_days is not None and dte is not None:
            details.append(
                _rule("earnings_beyond_dte", earnings_days > dte, earnings_days, f">{dte}")
            )
        else:
            skipped.append("earnings_beyond_dte")

        return details, skipped

    def _check_iron_strategy_rules(
        self, strategy_type: str, context: dict, reasoning: dict
    ) -> tuple[list[dict], list[str]]:
        try:
            params = get_strategy_entry_params(strategy_type)
        except Exception:
            params = {}

        details: list[dict] = []
        skipped: list[str] = []

        # IVR floor ≥ 50
        iv_rank = _get_iv_rank(context)
        iv_rank_min = params.get("iv_rank_min", 50)
        if iv_rank is not None:
            details.append(_rule("ivr_floor", iv_rank >= iv_rank_min, iv_rank, f">={iv_rank_min}"))
        else:
            skipped.append("ivr_floor")

        # IV environment must be HIGH
        iv_env = context.get("iv_environment")
        if iv_env is not None:
            details.append(_rule("iv_environment_high", iv_env == "HIGH", iv_env, "HIGH"))
        else:
            skipped.append("iv_environment_high")

        # Market regime must be NEUTRAL
        regime = context.get("confirmed_market_regime")
        if regime is not None:
            details.append(_rule("regime_neutral", regime == "NEUTRAL", regime, "NEUTRAL"))
        else:
            skipped.append("regime_neutral")

        # DTE range
        dte = _get_dte(reasoning, context)
        if dte is not None:
            dte_min = params.get("dte_min", 20)
            dte_max = params.get("dte_max", 50)
            details.append(
                _rule("dte_range", dte_min <= dte <= dte_max, dte, f"{dte_min}–{dte_max}")
            )
        else:
            skipped.append("dte_range")

        # Total credit floor
        total_credit = reasoning.get("total_credit")
        if total_credit is not None:
            min_credit = params.get("min_total_credit", 1.00)
            details.append(
                _rule("total_credit_floor", float(total_credit) >= min_credit, total_credit, f">={min_credit}")
            )
        else:
            skipped.append("total_credit_floor")

        # Earnings buffer (30 days for iron strategies)
        earnings_days = _get_earnings_days(context)
        earnings_buffer = params.get("earnings_buffer_days", 30)
        if earnings_days is not None:
            details.append(
                _rule("earnings_buffer", earnings_days > earnings_buffer, earnings_days, f">{earnings_buffer}")
            )
        else:
            skipped.append("earnings_buffer")

        return details, skipped

    def _check_calendar_spread_rules(
        self, context: dict, reasoning: dict
    ) -> tuple[list[dict], list[str]]:
        try:
            params = get_strategy_entry_params("calendar_spread")
        except Exception:
            params = {}

        details: list[dict] = []
        skipped: list[str] = []

        # IV environment must be LOW or MODERATE
        iv_env = context.get("iv_environment")
        if iv_env is not None:
            allowed = ["LOW", "MODERATE"]
            details.append(_rule("iv_environment", iv_env in allowed, iv_env, "LOW or MODERATE"))
        else:
            skipped.append("iv_environment")

        # DTE (short leg)
        dte = _get_dte(reasoning, context, keys=("short_dte", "dte"))
        if dte is not None:
            dte_min = params.get("short_dte_min", 20)
            dte_max = params.get("short_dte_max", 40)
            details.append(
                _rule("dte_range", dte_min <= dte <= dte_max, dte, f"{dte_min}–{dte_max}")
            )
        else:
            skipped.append("dte_range")

        # Net debit in range
        net_debit = reasoning.get("net_debit")
        if net_debit is not None:
            min_debit = params.get("min_net_debit", 0.50)
            max_debit = params.get("max_net_debit", 3.00)
            passed = min_debit <= float(net_debit) <= max_debit
            details.append(_rule("net_debit_range", passed, net_debit, f"{min_debit}–{max_debit}"))
        else:
            skipped.append("net_debit_range")

        # Earnings buffer
        earnings_days = _get_earnings_days(context)
        earnings_buffer = params.get("earnings_buffer_days", 45)
        if earnings_days is not None:
            details.append(
                _rule("earnings_buffer", earnings_days > earnings_buffer, earnings_days, f">{earnings_buffer}")
            )
        else:
            skipped.append("earnings_buffer")

        return details, skipped

    # ── skip_validity_structural ──────────────────────────────────────────────

    def _score_skip_validity_structural(self, decision: dict) -> dict:
        """Score structural validity of a SKIP decision.

        Checks:
        1. skip_reason_code is a known SkipCode enum value.
        2. The reason is contextually consistent (e.g. if code is
           EARNINGS_TOO_CLOSE the context must show earnings within
           the block window).

        Returns score 1.0 (pass) or 0.0 (fail).
        """
        decision_id = decision.get("id")
        code = decision.get("skip_reason_code") or ""
        strategy_type = decision.get("strategy_type") or ""
        context = decision.get("context") or {}
        details: list[dict] = []

        # Check 1: valid enum value
        code_valid = code in SkipCode.ALL
        details.append({
            "check": "code_is_valid_enum",
            "passed": code_valid,
            "observed": code,
            "expected": "one of SkipCode.ALL",
        })

        if not code_valid:
            # No point running context checks for unknown codes
            return {
                "decision_id": decision_id,
                "dimension": "skip_validity_structural",
                "score": 0.0,
                "score_metadata": {
                    "skip_reason_code": code,
                    "details": details,
                    "context_checks": [],
                },
            }

        # Check 2: contextual consistency (code-specific)
        context_checks: list[dict] = []

        if code == SkipCode.EARNINGS_TOO_CLOSE:
            context_checks.append(
                self._check_earnings_context_consistent(strategy_type, context)
            )
        elif code == SkipCode.LOW_IVR:
            context_checks.append(
                self._check_low_ivr_context_consistent(strategy_type, context)
            )
        elif code == SkipCode.HIGH_IVR:
            context_checks.append(
                self._check_high_ivr_context_consistent(context)
            )
        elif code == SkipCode.IV_ENV_MISMATCH:
            context_checks.append(
                self._check_iv_env_mismatch_consistent(strategy_type, context)
            )
        elif code == SkipCode.REGIME_MISMATCH:
            context_checks.append(
                self._check_regime_mismatch_consistent(strategy_type, context)
            )
        # CONFIDENCE_LOW, OTHER, and remaining codes are always contextually
        # consistent — no context data can contradict a confidence-based skip.

        context_passed = all(c["passed"] for c in context_checks)
        all_passed = code_valid and context_passed
        score = 1.0 if all_passed else 0.0

        return {
            "decision_id": decision_id,
            "dimension": "skip_validity_structural",
            "score": score,
            "score_metadata": {
                "skip_reason_code": code,
                "details": details,
                "context_checks": context_checks,
            },
        }

    def _check_earnings_context_consistent(
        self, strategy_type: str, context: dict
    ) -> dict:
        """EARNINGS_TOO_CLOSE: earnings must be within the block window."""
        earnings_days = _get_earnings_days(context)
        if earnings_days is None:
            # Data absent — benefit of the doubt
            return {
                "check": "earnings_within_block_window",
                "passed": True,
                "observed": None,
                "note": "earnings data absent — skipped",
            }

        try:
            params = get_strategy_entry_params(strategy_type)
            # For wheel-type strategies, use the CSP block as the reference
            if strategy_type in ("wheel", "turnover_wheel"):
                params = params.get("csp", params)
            block_days = params.get("earnings_buffer_days", _DEFAULT_EARNINGS_BUFFER_DAYS)
        except Exception:
            block_days = _DEFAULT_EARNINGS_BUFFER_DAYS

        # Consistent: earnings are at or within the block window
        consistent = earnings_days <= block_days
        return {
            "check": "earnings_within_block_window",
            "passed": consistent,
            "observed": earnings_days,
            "threshold": block_days,
            "note": (
                f"earnings in {earnings_days} days — "
                f"{'within' if consistent else 'outside'} {block_days}-day block"
            ),
        }

    def _check_low_ivr_context_consistent(
        self, strategy_type: str, context: dict
    ) -> dict:
        """LOW_IVR: IVR must actually be below the strategy minimum."""
        iv_rank = _get_iv_rank(context)
        if iv_rank is None:
            return {
                "check": "ivr_below_minimum",
                "passed": True,
                "observed": None,
                "note": "iv_rank absent — skipped",
            }

        try:
            params = get_strategy_entry_params(strategy_type)
            if strategy_type in ("wheel", "turnover_wheel"):
                params = params.get("csp", params)
            iv_min = params.get("iv_rank_min", 30)
        except Exception:
            iv_min = 30

        consistent = iv_rank < iv_min
        return {
            "check": "ivr_below_minimum",
            "passed": consistent,
            "observed": iv_rank,
            "threshold": iv_min,
            "note": (
                f"iv_rank={iv_rank} {'<' if consistent else '>='} minimum {iv_min}"
            ),
        }

    def _check_high_ivr_context_consistent(self, context: dict) -> dict:
        """HIGH_IVR: IVR must actually be high (above approximate high threshold)."""
        iv_rank = _get_iv_rank(context)
        if iv_rank is None:
            return {
                "check": "ivr_above_high_threshold",
                "passed": True,
                "observed": None,
                "note": "iv_rank absent — skipped",
            }
        consistent = iv_rank > _HIGH_IVR_THRESHOLD
        return {
            "check": "ivr_above_high_threshold",
            "passed": consistent,
            "observed": iv_rank,
            "threshold": _HIGH_IVR_THRESHOLD,
            "note": f"iv_rank={iv_rank} {'>' if consistent else '<='} {_HIGH_IVR_THRESHOLD}",
        }

    def _check_iv_env_mismatch_consistent(
        self, strategy_type: str, context: dict
    ) -> dict:
        """IV_ENV_MISMATCH: IV environment must not be in strategy's allowed list."""
        iv_env = context.get("iv_environment")
        if iv_env is None:
            return {
                "check": "iv_env_not_allowed",
                "passed": True,
                "observed": None,
                "note": "iv_environment absent — skipped",
            }
        try:
            from strategies.strategy_loader import load_strategy
            strat = load_strategy(strategy_type)
            allowed = strat.get("iv_environment", {}).get("allowed", [])
        except Exception:
            allowed = []

        if not allowed:
            return {
                "check": "iv_env_not_allowed",
                "passed": True,
                "observed": iv_env,
                "note": "strategy allowed list unavailable — skipped",
            }

        consistent = iv_env not in allowed
        return {
            "check": "iv_env_not_allowed",
            "passed": consistent,
            "observed": iv_env,
            "allowed": allowed,
            "note": (
                f"iv_environment={iv_env!r} "
                f"{'not in' if consistent else 'IS IN'} allowed {allowed}"
            ),
        }

    def _check_regime_mismatch_consistent(
        self, strategy_type: str, context: dict
    ) -> dict:
        """REGIME_MISMATCH: market regime must not be in strategy's allowed list."""
        regime = context.get("confirmed_market_regime")
        if regime is None:
            return {
                "check": "regime_not_allowed",
                "passed": True,
                "observed": None,
                "note": "confirmed_market_regime absent — skipped",
            }
        try:
            from strategies.strategy_loader import load_strategy
            strat = load_strategy(strategy_type)
            allowed = strat.get("regime", {}).get("allowed", [])
        except Exception:
            allowed = []

        if not allowed:
            return {
                "check": "regime_not_allowed",
                "passed": True,
                "observed": regime,
                "note": "strategy allowed regimes unavailable — skipped",
            }

        consistent = regime not in allowed
        return {
            "check": "regime_not_allowed",
            "passed": consistent,
            "observed": regime,
            "allowed": allowed,
            "note": (
                f"regime={regime!r} "
                f"{'not in' if consistent else 'IS IN'} allowed {allowed}"
            ),
        }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _rule(name: str, passed: bool, observed: Any, threshold: Any) -> dict:
    """Build a single rule-result dict."""
    return {
        "rule": name,
        "passed": passed,
        "observed": observed,
        "threshold": threshold,
    }


def _parse_reasoning(reasoning: Any) -> dict:
    """Return reasoning as a dict, parsing from JSON string if needed."""
    if isinstance(reasoning, dict):
        return reasoning
    if isinstance(reasoning, str):
        try:
            parsed = json.loads(reasoning)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _get_iv_rank(context: dict) -> float | None:
    """Extract IV rank from context, trying multiple known paths."""
    iv = context.get("iv_rank")
    if iv is None:
        iv = (context.get("volatility") or {}).get("iv_rank_1y")
    if iv is None:
        iv = (context.get("volatility") or {}).get("iv_rank")
    try:
        return float(iv) if iv is not None else None
    except (TypeError, ValueError):
        return None


def _get_earnings_days(context: dict) -> float | None:
    """Extract days until earnings from context, trying multiple paths."""
    days = (context.get("earnings") or {}).get("days_until_earnings")
    if days is None:
        days = (context.get("fundamentals") or {}).get("days_to_earnings")
    try:
        return float(days) if days is not None else None
    except (TypeError, ValueError):
        return None


def _get_delta(
    reasoning: dict,
    context: dict,
    keys: tuple[str, ...] = ("delta",),
) -> float | None:
    """Extract delta from reasoning dict or context, trying multiple keys."""
    for k in keys:
        val = reasoning.get(k)
        if val is not None:
            try:
                return abs(float(val))
            except (TypeError, ValueError):
                continue
    # Fall back to context selected_option
    sel = (context.get("selected_option") or {})
    for k in keys:
        val = sel.get(k)
        if val is not None:
            try:
                return abs(float(val))
            except (TypeError, ValueError):
                continue
    return None


def _get_dte(
    reasoning: dict,
    context: dict,
    keys: tuple[str, ...] = ("dte",),
) -> int | None:
    """Extract DTE from reasoning dict or context."""
    for k in keys:
        val = reasoning.get(k)
        if val is not None:
            try:
                return int(float(val))
            except (TypeError, ValueError):
                continue
    sel = (context.get("selected_option") or {})
    for k in keys:
        val = sel.get(k)
        if val is not None:
            try:
                return int(float(val))
            except (TypeError, ValueError):
                continue
    return None


def _get_open_interest(reasoning: dict, context: dict) -> int | None:
    """Extract open interest from reasoning or context."""
    val = reasoning.get("open_interest")
    if val is None:
        val = (context.get("option_liquidity") or {}).get("open_interest")
    if val is None:
        val = (context.get("selected_option") or {}).get("open_interest")
    try:
        return int(float(val)) if val is not None else None
    except (TypeError, ValueError):
        return None
