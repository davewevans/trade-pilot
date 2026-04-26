"""Regression test: yfinance must not reappear in paths migrated away from it.

After yfinance migration Stage 6, the following files have been migrated to
Alpaca + FRED. Reintroducing yfinance to them silently regresses the migration.

The ex-dividend yfinance fallback in market_data.py is the only remaining
intentional yfinance use until the post-Stage-6 cleanup PR.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

SCOPE_LOCKED_FILES = [
    REPO_ROOT / "backtesting" / "engine.py",
    REPO_ROOT / "data" / "context_builder.py",
]


def test_migrated_files_have_no_yfinance_imports():
    for path in SCOPE_LOCKED_FILES:
        text = path.read_text(encoding="utf-8")
        assert "import yfinance" not in text, (
            f"{path} reintroduced yfinance — this file was migrated to "
            f"Alpaca/FRED in Stage 6. If yfinance is genuinely needed again, "
            f"update SCOPE_LOCKED_FILES with rationale."
        )
        assert "yf.download" not in text, (
            f"{path} contains yf.download — see above."
        )
        assert "yf.Ticker" not in text, (
            f"{path} contains yf.Ticker — see above."
        )
