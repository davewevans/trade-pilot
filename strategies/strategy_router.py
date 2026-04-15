"""Strategy routing layer — selects which strategies run each decision cycle.

Reads the shared context (regime, IV, circuit breaker) and decides which
strategies should run.  At most ONE spread strategy opens a new position
per cycle.  Management cycles (OPEN state) always run.
"""

import logging

logger = logging.getLogger(__name__)

# Priority order: income strategies before speculative
# Eligibility is now read from strategy definition JSON files.
_IDLE_PRIORITY = [
    "iron_condor",
    "short_strangle",
    "bull_put_spread",
    "bear_call_spread",
    "long_call_vertical",
]


class StrategyRouter:
    """Decides which strategies should run their decision cycles this tick."""

    def get_active_strategies(
        self,
        context: dict,
        strategy_states: dict[str, str],
        circuit_breaker_status: str = "GREEN",
    ) -> list[str]:
        """Return strategy names that should run this cycle.

        Always includes "wheel".  For spread strategies, always includes
        those in OPEN state (need management).  For IDLE strategies,
        selects at most one new-position candidate based on routing rules.

        Args:
            context: The shared context dict from ContextBuilder.
            strategy_states: ``{"iron_condor": "IDLE", ...}`` for each strategy.
            circuit_breaker_status: "GREEN", "YELLOW", or "RED".

        Returns:
            List of strategy name strings.
        """
        active: list[str] = ["wheel"]

        regime = context.get("confirmed_market_regime", "NEUTRAL")

        # CRASH: only wheel management
        if regime == "CRASH":
            logger.info("Router: CRASH regime — wheel only")
            return active

        # RED: no new positions at all
        if circuit_breaker_status == "RED":
            logger.info("Router: circuit breaker RED — management only")
            # Still run management for open positions
            for name in _IDLE_PRIORITY:
                if strategy_states.get(name) == "OPEN":
                    active.append(name)
            return active

        # Always include OPEN-state strategies (need management)
        for name in _IDLE_PRIORITY:
            if strategy_states.get(name) == "OPEN":
                active.append(name)

        # YELLOW: management only, no new entries
        if circuit_breaker_status == "YELLOW":
            logger.info("Router: circuit breaker YELLOW — management only")
            return active

        # GREEN: pick one IDLE strategy for a new position
        iv_env = context.get("iv_environment", "MODERATE")
        ivr = context.get("iv_rank")
        bounce = context.get("support_bounce_signal") or {}
        cahold = bounce.get("cahold_detected", False)

        candidates = self._get_idle_candidates(regime, iv_env, ivr, cahold, strategy_states)

        if candidates:
            chosen = candidates[0]  # highest priority
            active.append(chosen)
            logger.info("Router: selected %s for new entry evaluation", chosen)
        else:
            logger.debug("Router: no idle strategy candidates this cycle")

        return active

    @staticmethod
    def _get_idle_candidates(
        regime: str,
        iv_env: str,
        ivr: float | None,
        cahold: bool,
        strategy_states: dict[str, str],
    ) -> list[str]:
        """Return IDLE strategies eligible for entry, in priority order.

        Reads regime and IV environment eligibility from strategy definition
        JSON files.  Missing or malformed definition files are logged and
        skipped — the router never crashes due to a bad definition file.
        """
        from strategies.strategy_loader import load_all_strategies

        try:
            all_strategies = load_all_strategies()
        except Exception:
            logger.warning("Router: failed to load strategy definitions", exc_info=True)
            all_strategies = {}

        candidates: list[str] = []

        for name in _IDLE_PRIORITY:
            if strategy_states.get(name) != "IDLE":
                continue

            defn = all_strategies.get(name)
            if not defn:
                logger.warning("Router: no definition found for %s — skipping", name)
                continue
            if defn.get("composite"):
                continue

            # Regime eligibility
            allowed_regimes = defn.get("regime", {}).get("allowed", [])
            if allowed_regimes and regime not in allowed_regimes:
                continue

            # IV environment eligibility
            allowed_iv = defn.get("iv_environment", {}).get("allowed", [])
            if allowed_iv and iv_env not in allowed_iv:
                continue

            # Strategy-specific extra checks from entry params
            entry = defn.get("entry", {})
            if name == "iron_condor":
                min_ivr = entry.get("iv_rank_min", 50)
                if ivr is not None and ivr < min_ivr:
                    continue
            elif name == "long_call_vertical":
                if entry.get("require_cahold_signal") and not cahold:
                    continue

            candidates.append(name)

        return candidates
