"""Loads strategy definition JSON files from strategies/definitions/.

Provides:
- load_strategy(name) -> dict
- load_all_strategies() -> dict[str, dict]
- get_strategy_entry_params(name) -> dict
- get_strategy_guardrail_params(name) -> dict
- get_strategy_management_params(name) -> dict
- list_available_strategies() -> list[str]
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"


@lru_cache(maxsize=None)
def load_strategy(name: str) -> dict:
    """Load a strategy definition by name. Raises FileNotFoundError if missing."""
    path = _DEFINITIONS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Strategy definition not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    for field in ("name", "display_name", "type"):
        if field not in data:
            raise ValueError(f"Strategy {name} missing required field: {field}")
    return data


def load_all_strategies() -> dict[str, dict]:
    """Load all strategy definitions. Returns dict keyed by strategy name."""
    result = {}
    for path in _DEFINITIONS_DIR.glob("*.json"):
        try:
            data = load_strategy(path.stem)
            result[data["name"]] = data
        except Exception:
            logger.exception("Failed to load strategy definition: %s", path)
    return result


def list_available_strategies() -> list[str]:
    """Return names of all available strategy definitions."""
    return [p.stem for p in _DEFINITIONS_DIR.glob("*.json")]


def get_strategy_entry_params(name: str) -> dict:
    """Return the entry parameters for a strategy."""
    return load_strategy(name).get("entry", {})


def get_strategy_guardrail_params(name: str) -> dict:
    """Return the guardrail parameters for a strategy."""
    return load_strategy(name).get("guardrails", {})


def get_strategy_management_params(name: str) -> dict:
    """Return the management parameters for a strategy."""
    return load_strategy(name).get("management", {})


def invalidate_cache() -> None:
    """Clear the LRU cache. Call after strategy files are modified."""
    load_strategy.cache_clear()
