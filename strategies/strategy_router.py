"""Strategy routing layer — selects which strategies run each decision cycle.

Reads the shared context (regime, IV, circuit breaker) and decides which
strategies should run.  At most ONE spread strategy opens a new position
per cycle.  Management cycles (OPEN state) always run.
"""

import logging

logger = logging.getLogger(__name__)

# Priority order: income strategies before speculative
_IDLE_PRIORITY = [
    "iron_condor",
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
        """Return IDLE strategies eligible for entry, in priority order."""
        candidates: list[str] = []

        for name in _IDLE_PRIORITY:
            if strategy_states.get(name) != "IDLE":
                continue

            if name == "iron_condor":
                if regime == "NEUTRAL" and iv_env == "HIGH":
                    candidates.append(name)

            elif name == "bull_put_spread":
                if regime in ("NEUTRAL", "BULL") and iv_env in ("MODERATE", "HIGH"):
                    candidates.append(name)

            elif name == "bear_call_spread":
                if regime in ("BEAR", "NEUTRAL") and iv_env in ("MODERATE", "HIGH"):
                    candidates.append(name)

            elif name == "long_call_vertical":
                if regime == "BULL" and iv_env == "LOW" and cahold:
                    candidates.append(name)

        return candidates
