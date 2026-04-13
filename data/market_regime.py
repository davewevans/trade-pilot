"""Market regime derivation and stability filtering.

Derives a market regime (CRASH, BEAR, NEUTRAL, BULL, EUPHORIA) from
VIX, SPX trend, and Fear & Greed data already present in the context.

The ``RegimeStabilityFilter`` prevents regime flapping by requiring N
consecutive identical readings before confirming a regime change.
"""

import json
import logging
import statistics
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Strategy routing hints ──────────────────────────────────

_ROUTING_HINTS: dict[str, str] = {
    "CRASH": (
        "Extreme caution. No new positions. "
        "Manage existing positions only."
    ),
    "BEAR": (
        "Defensive posture. Prefer short call verticals on "
        "bear-trending underlyings. Avoid new CSPs."
    ),
    "NEUTRAL": (
        "Standard wheel and iron condor conditions. "
        "Apply normal filters."
    ),
    "BULL": (
        "Favorable for CSPs and short put verticals. "
        "Covered calls should be placed higher than normal."
    ),
    "EUPHORIA": (
        "Late-cycle caution. Reduce position sizes 25%. "
        "Prefer defined-risk spreads over naked CSPs."
    ),
}


# ── Regime derivation ──────────────────────────────────────


def derive_market_regime(context: dict) -> dict:
    """Add ``market_regime``, ``iv_environment``, and routing hint to *context*.

    Reads VIX, Fear & Greed, and SPX trend indicators from the context's
    ``macro`` and ``spx_technicals`` sections.  If SPX technicals are missing,
    falls back to the per-symbol ``technicals`` section (usually SPY).

    The function mutates *context* in place and also returns it for chaining.
    """
    macro = context.get("macro") or {}
    vix = macro.get("vix")
    fg_score = macro.get("fear_greed_score")

    # SPX trend — prefer dedicated spx_technicals, fall back to technicals
    spx = context.get("spx_technicals") or context.get("technicals") or {}
    above_sma_50 = spx.get("above_sma_50")
    above_sma_200 = spx.get("above_sma_200")
    below_sma_50 = above_sma_50 is False
    below_sma_200 = above_sma_200 is False

    # ── Regime decision (priority order) ────────────────────
    regime = _classify_regime(vix, below_sma_50, below_sma_200, above_sma_50, fg_score)

    context["market_regime"] = regime
    context["strategy_routing_hint"] = _ROUTING_HINTS.get(regime, _ROUTING_HINTS["NEUTRAL"])

    # ── IV environment ──────────────────────────────────────
    iv_ranks: list[float] = []
    # Collect from per-symbol iv_rank list if present
    for ivr in context.get("iv_ranks", []):
        if ivr is not None:
            iv_ranks.append(float(ivr))
    # Single-symbol fallback
    single_ivr = context.get("iv_rank")
    if single_ivr is not None and not iv_ranks:
        iv_ranks.append(float(single_ivr))

    context["iv_environment"] = _classify_iv_environment(iv_ranks)

    return context


def _classify_regime(
    vix: float | None,
    below_sma_50: bool,
    below_sma_200: bool,
    above_sma_50: bool | None,
    fg_score: float | None,
) -> str:
    """Return the market regime string based on threshold priority."""
    if vix is not None and vix >= 35:
        return "CRASH"

    if vix is not None and vix >= 25 and below_sma_200:
        return "BEAR"

    if vix is not None and vix >= 20 and below_sma_50:
        return "BEAR"

    if (
        vix is not None
        and vix <= 14
        and above_sma_50 is True
        and fg_score is not None
        and fg_score >= 75
    ):
        return "EUPHORIA"

    if vix is not None and vix <= 18 and above_sma_50 is True:
        return "BULL"

    return "NEUTRAL"


def _classify_iv_environment(iv_ranks: list[float]) -> str:
    """Return LOW / MODERATE / HIGH based on median IV rank."""
    if not iv_ranks:
        return "MODERATE"
    median = statistics.median(iv_ranks)
    if median < 30:
        return "LOW"
    if median > 50:
        return "HIGH"
    return "MODERATE"


# ── Stability filter ───────────────────────────────────────


class RegimeStabilityFilter:
    """Requires N consecutive identical readings before confirming a change.

    Persists history to ``data/snapshots/regime_history.json``.
    """

    def __init__(
        self,
        history_path: Path | None = None,
        required_readings: int = 3,
    ) -> None:
        self.required_readings = required_readings

        if history_path is not None:
            self._path = history_path
        else:
            from config import settings
            self._path = settings.SNAPSHOTS_DIR / "regime_history.json"

        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._readings: list[str] = []
        self._confirmed: str = "NEUTRAL"
        self._load()

    # ── persistence ─────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._readings = data.get("readings", [])
            self._confirmed = data.get("confirmed", "NEUTRAL")
        except Exception:
            logger.exception("Failed to load regime history — starting fresh")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        import os
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({
                "readings": self._readings,
                "confirmed": self._confirmed,
            }, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(self._path))

    def reset(self) -> None:
        """Clear readings/confirmed regime. Intended for tests."""
        self._readings = []
        self._confirmed = "NEUTRAL"
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception:
            pass

    # ── public API ──────────────────────────────────────────

    def record_reading(self, regime: str) -> bool:
        """Record a new regime reading.

        Returns True if the *confirmed* regime changed as a result.
        """
        self._readings.append(regime)
        # Keep only the last N readings
        if len(self._readings) > self.required_readings:
            self._readings = self._readings[-self.required_readings:]

        previous = self._confirmed

        # Confirm only if all N readings agree
        if (
            len(self._readings) >= self.required_readings
            and len(set(self._readings)) == 1
        ):
            self._confirmed = self._readings[0]

        self._save()
        changed = self._confirmed != previous
        if changed:
            logger.info(
                "Market regime confirmed: %s → %s (after %d consistent readings)",
                previous, self._confirmed, self.required_readings,
            )
        return changed

    def get_confirmed_regime(self) -> str:
        """Return the last confirmed (stable) regime."""
        return self._confirmed

    def is_stable(self) -> bool:
        """Return True if the last N readings all agree."""
        if len(self._readings) < self.required_readings:
            return False
        return len(set(self._readings)) == 1
