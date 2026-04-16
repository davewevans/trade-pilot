"""Candidate universe — load, persist, and manage the symbol list.

The universe is the set of symbols the research layer tracks. It starts
with S&P 500 constituents + major ETFs, and accepts operator-controlled
manual additions and exclusions.

Stored in data/candidate_universe.json (operator-maintained; quarterly
refresh is a manual task per the Phase 1 spec).
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ETFS: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "DIA",
    "XLE", "XLF", "XLK", "XLV", "XLY", "XLI", "XLP", "XLU", "XLRE", "XLB", "XLC",
    "GLD", "SLV", "TLT", "HYG", "LQD",
    "USO", "UNG", "FXI", "EEM", "EFA",
)


@dataclass
class CandidateUniverseData:
    version: int
    updated_at: str
    source: str
    symbols: list[str]
    manual_adds: list[str]
    manual_excludes: list[str]


class CandidateUniverse:
    """Load and maintain the candidate_universe.json file.

    Usage::

        universe = CandidateUniverse()
        universe.load()
        symbols = universe.all_symbols()
    """

    def __init__(self, path: Path | str | None = None):
        if path is not None:
            self._path = Path(path)
        else:
            # Lazy import to avoid circular imports at module load time.
            from config import settings
            self._path = settings.DATA_DIR / "candidate_universe.json"
        self._data: CandidateUniverseData | None = None

    # ── I/O ──────────────────────────────────────────────────────────────

    def load(self) -> CandidateUniverseData:
        """Read and return the universe. Returns empty universe if file missing."""
        if not self._path.exists():
            logger.warning(
                "candidate_universe.json not found at %s — returning empty universe",
                self._path,
            )
            self._data = CandidateUniverseData(
                version=1,
                updated_at="",
                source="",
                symbols=[],
                manual_adds=[],
                manual_excludes=[],
            )
            return self._data

        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._data = CandidateUniverseData(
            version=raw.get("version", 1),
            updated_at=raw.get("updated_at", ""),
            source=raw.get("source", ""),
            symbols=[s.upper() for s in raw.get("symbols", [])],
            manual_adds=[s.upper() for s in raw.get("manual_adds", [])],
            manual_excludes=[s.upper() for s in raw.get("manual_excludes", [])],
        )
        return self._data

    def save(self) -> None:
        """Persist the current universe to disk."""
        if self._data is None:
            raise RuntimeError("load() must be called before save()")
        payload = {
            "version": self._data.version,
            "updated_at": self._data.updated_at,
            "source": self._data.source,
            "symbols": sorted(set(self._data.symbols)),
            "manual_adds": sorted(set(self._data.manual_adds)),
            "manual_excludes": sorted(set(self._data.manual_excludes)),
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ── Mutation ─────────────────────────────────────────────────────────

    def bootstrap(
        self,
        sp500_symbols: list[str],
        updated_at: str,
        include_default_etfs: bool = True,
    ) -> None:
        """Rebuild the base symbols list, preserving manual adds/excludes."""
        if self._data is None:
            raise RuntimeError("load() must be called before bootstrap()")
        base = [s.upper() for s in sp500_symbols]
        if include_default_etfs:
            base += list(DEFAULT_ETFS)
        self._data.symbols = sorted(set(base))
        self._data.updated_at = updated_at

    def add_manual(self, symbol: str) -> None:
        """Add a symbol to manual_adds (case-insensitive, idempotent)."""
        if self._data is None:
            raise RuntimeError("load() must be called before add_manual()")
        sym = symbol.upper()
        if sym not in self._data.manual_adds:
            self._data.manual_adds.append(sym)

    def exclude_manual(self, symbol: str) -> None:
        """Add a symbol to manual_excludes (case-insensitive, idempotent)."""
        if self._data is None:
            raise RuntimeError("load() must be called before exclude_manual()")
        sym = symbol.upper()
        if sym not in self._data.manual_excludes:
            self._data.manual_excludes.append(sym)

    def remove_manual_add(self, symbol: str) -> None:
        """Remove a symbol from manual_adds (idempotent)."""
        if self._data is None:
            raise RuntimeError("load() must be called before remove_manual_add()")
        sym = symbol.upper()
        self._data.manual_adds = [s for s in self._data.manual_adds if s != sym]

    def remove_manual_exclude(self, symbol: str) -> None:
        """Remove a symbol from manual_excludes (idempotent)."""
        if self._data is None:
            raise RuntimeError("load() must be called before remove_manual_exclude()")
        sym = symbol.upper()
        self._data.manual_excludes = [s for s in self._data.manual_excludes if s != sym]

    # ── Query ─────────────────────────────────────────────────────────────

    def all_symbols(self) -> list[str]:
        """Merged, deduped, sorted list: (symbols ∪ manual_adds) − manual_excludes."""
        if self._data is None:
            raise RuntimeError("load() must be called before all_symbols()")
        merged = set(self._data.symbols) | set(self._data.manual_adds)
        merged -= set(self._data.manual_excludes)
        return sorted(merged)

    def contains(self, symbol: str) -> bool:
        return symbol.upper() in set(self.all_symbols())

    def count(self) -> int:
        return len(self.all_symbols())
