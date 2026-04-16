"""One-off script: populate candidate_universe.json with S&P 500 constituents.

Run quarterly when the index reconstitutes. Preserves manual_adds and
manual_excludes across runs.

Usage:
    py scripts/bootstrap_sp500.py                 # update in place
    py scripts/bootstrap_sp500.py --dry-run       # report only, no write
    py scripts/bootstrap_sp500.py --source github # force GitHub CSV
    py scripts/bootstrap_sp500.py --source wikipedia
    py scripts/bootstrap_sp500.py --force         # skip <450 sanity check
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.candidates.universe import CandidateUniverse

# ── Source URLs ────────────────────────────────────────────────────────────────

GITHUB_CSV_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies"
    "/main/data/constituents.csv"
)
WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

SANITY_FLOOR = 450  # abort if fewer than this many symbols are fetched


# ── Fetchers ───────────────────────────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 20) -> str:
    """Fetch a URL and return the body as a string."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "trade-pilot/bootstrap_sp500 (github.com/trade-pilot)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_github_csv(text: str) -> list[str]:
    """Parse the datasets/s-and-p-500-companies CSV.

    First column is 'Symbol'. Replaces '.' with '-' (e.g. BRK.B -> BRK-B)
    to match options market ticker format.
    """
    reader = csv.DictReader(io.StringIO(text))
    symbols = []
    for row in reader:
        sym = row.get("Symbol", "").strip().upper().replace(".", "-")
        if sym:
            symbols.append(sym)
    return symbols


def _parse_wikipedia_html(html: str) -> list[str]:
    """Extract tickers from Wikipedia's S&P 500 table.

    Requires the 'html.parser' stdlib module only (no pandas needed).
    Wikipedia uses '.' as a separator (e.g. BRK.B); options markets use '-'.
    """
    try:
        from html.parser import HTMLParser

        class _TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self._in_table = False
                self._in_tbody = False
                self._in_tr = False
                self._col = 0
                self._in_td = False
                self._current_text = []
                self.symbols: list[str] = []
                # Detect when we're inside the constituents table
                self._table_count = 0

            def handle_starttag(self, tag, attrs):
                attr_dict = dict(attrs)
                if tag == "table":
                    self._table_count += 1
                    # The first wikitable with id="constituents" or first
                    # sortable wikitable is what we want.
                    classes = attr_dict.get("class", "")
                    if "wikitable" in classes and not self._in_table:
                        self._in_table = True
                elif tag == "tbody" and self._in_table:
                    self._in_tbody = True
                elif tag == "tr" and self._in_tbody:
                    self._in_tr = True
                    self._col = 0
                elif tag == "td" and self._in_tr:
                    self._in_td = True
                    self._current_text = []

            def handle_endtag(self, tag):
                if tag == "table" and self._in_table:
                    self._in_table = False
                    self._in_tbody = False
                elif tag == "tbody" and self._in_tbody:
                    self._in_tbody = False
                elif tag == "tr" and self._in_tr:
                    self._in_tr = False
                elif tag == "td" and self._in_td:
                    if self._col == 0:
                        raw = "".join(self._current_text).strip()
                        sym = raw.replace(".", "-").upper()
                        if sym:
                            self.symbols.append(sym)
                    self._col += 1
                    self._in_td = False
                    self._current_text = []

            def handle_data(self, data):
                if self._in_td:
                    self._current_text.append(data)

        parser = _TableParser()
        parser.feed(html)
        return parser.symbols

    except Exception as exc:
        raise ValueError(f"Failed to parse Wikipedia HTML: {exc}") from exc


def fetch_github(verbose: bool = True) -> list[str]:
    if verbose:
        print(f"  Fetching from GitHub: {GITHUB_CSV_URL}")
    text = _fetch_url(GITHUB_CSV_URL)
    symbols = _parse_github_csv(text)
    if verbose:
        print(f"  Parsed {len(symbols)} symbols from GitHub CSV")
    return symbols


def fetch_wikipedia(verbose: bool = True) -> list[str]:
    if verbose:
        print(f"  Fetching from Wikipedia: {WIKIPEDIA_URL}")
    html = _fetch_url(WIKIPEDIA_URL)
    symbols = _parse_wikipedia_html(html)
    if verbose:
        print(f"  Parsed {len(symbols)} symbols from Wikipedia")
    return symbols


# ── Main logic ─────────────────────────────────────────────────────────────────

def fetch_sp500(source: str = "auto", verbose: bool = True) -> list[str]:
    """Fetch S&P 500 tickers using the requested source strategy.

    source: 'auto' | 'github' | 'wikipedia'
    Returns a list of uppercase ticker strings.
    Raises RuntimeError if all attempted sources fail.
    """
    errors: list[str] = []

    if source in ("auto", "github"):
        try:
            symbols = fetch_github(verbose=verbose)
            if symbols:
                return symbols
            errors.append("GitHub CSV returned empty list")
        except Exception as exc:
            errors.append(f"GitHub fetch failed: {exc}")
            if source == "github":
                raise RuntimeError("; ".join(errors)) from exc
            if verbose:
                print(f"  GitHub source failed ({exc}), trying Wikipedia…")

    if source in ("auto", "wikipedia"):
        try:
            symbols = fetch_wikipedia(verbose=verbose)
            if symbols:
                return symbols
            errors.append("Wikipedia returned empty list")
        except Exception as exc:
            errors.append(f"Wikipedia fetch failed: {exc}")

    raise RuntimeError(
        "All S&P 500 sources failed:\n" + "\n".join(f"  - {e}" for e in errors)
    )


def run(
    dry_run: bool = False,
    source: str = "auto",
    force: bool = False,
    universe_path: Path | None = None,
) -> int:
    """Bootstrap the candidate universe. Returns exit code (0=success)."""
    print("=== bootstrap_sp500 ===")

    # ── Fetch tickers ──────────────────────────────────────────────────────
    print(f"\n[1/3] Fetching S&P 500 constituents (source={source})")
    try:
        sp500 = fetch_sp500(source=source, verbose=True)
    except RuntimeError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    # ── Sanity check ──────────────────────────────────────────────────────
    if len(sp500) < SANITY_FLOOR:
        msg = (
            f"Only {len(sp500)} symbols fetched — expected ≥{SANITY_FLOOR}. "
            "This looks wrong. Pass --force to override."
        )
        if force:
            print(f"WARNING: {msg} (--force passed, continuing)")
        else:
            print(f"\nABORTED: {msg}", file=sys.stderr)
            return 1

    # ── Load existing universe ─────────────────────────────────────────────
    print("\n[2/3] Loading existing universe")
    universe = CandidateUniverse(path=universe_path)
    data = universe.load()
    old_count = universe.count()
    old_symbols = set(universe.all_symbols())

    print(f"  Current symbols (all_symbols): {old_count}")
    if data.manual_adds:
        print(f"  Manual adds ({len(data.manual_adds)}): {', '.join(data.manual_adds)}")
    if data.manual_excludes:
        print(f"  Manual excludes ({len(data.manual_excludes)}): {', '.join(data.manual_excludes)}")

    # ── Bootstrap ──────────────────────────────────────────────────────────
    today = datetime.now(timezone.utc).date().isoformat()
    universe.bootstrap(sp500, updated_at=today, include_default_etfs=True)
    new_symbols = set(universe.all_symbols())
    new_count = universe.count()

    added = sorted(new_symbols - old_symbols)
    removed = sorted(old_symbols - new_symbols)

    print(f"\n[3/3] Changes")
    print(f"  Symbols: {old_count} -> {new_count}")
    if added:
        print(f"  Added ({len(added)}): {', '.join(added[:20])}" +
              (f" … and {len(added)-20} more" if len(added) > 20 else ""))
    if removed:
        print(f"  Removed ({len(removed)}): {', '.join(removed[:20])}" +
              (f" … and {len(removed)-20} more" if len(removed) > 20 else ""))
    if not added and not removed:
        print("  No changes — universe is already up to date")

    if data.manual_adds:
        preserved = [s for s in data.manual_adds if s in new_symbols]
        print(f"  Manual adds preserved: {', '.join(preserved) if preserved else 'none'}")
    if data.manual_excludes:
        print(f"  Manual excludes still active: {', '.join(data.manual_excludes)}")

    if dry_run:
        print("\n[DRY RUN] No changes written.")
        return 0

    universe.save()
    print(f"\nSaved {new_count} symbols to candidate_universe.json")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate candidate_universe.json with S&P 500 constituents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report what would change, but do not write to disk.",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "github", "wikipedia"],
        default="auto",
        help="Data source (default: auto — try GitHub then Wikipedia).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the <450-symbol sanity check.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(run(dry_run=args.dry_run, source=args.source, force=args.force))
