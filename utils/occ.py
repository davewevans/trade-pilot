"""Canonical OCC option symbol parsing utilities.

OCC option symbol format: ROOT(1-6 chars) + YYMMDD(6 digits) + C or P(1 char) + strike*1000(8 digits)
Examples: "AAPL260515P00260000", "SPY260502C00550000", "A260615P00120000"
"""

from __future__ import annotations

import re
from datetime import date, datetime

# 1-6 uppercase letters, 6 digits, C or P, 8 digits.
OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def parse_occ(symbol: str) -> dict | None:
    """Parse an OCC symbol into components. Returns None if invalid.

    Returns dict with: root, expiration (date), option_type ('C'/'P'),
    strike (float), expiration_str ('YYYY-MM-DD').
    """
    if not symbol:
        return None
    m = OCC_RE.match(symbol.upper())
    if not m:
        return None
    root, yymmdd, opt_type, strike_str = m.groups()
    try:
        exp = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError:
        return None
    strike = int(strike_str) / 1000.0
    return {
        "root": root,
        "expiration": exp,
        "option_type": opt_type,
        "strike": strike,
        "expiration_str": exp.isoformat(),
    }


def extract_root(symbol: str) -> str | None:
    """Extract the underlying root ticker from an OCC symbol."""
    p = parse_occ(symbol)
    return p["root"] if p else None


def extract_strike(symbol: str) -> float | None:
    """Extract the strike price from an OCC symbol."""
    p = parse_occ(symbol)
    return p["strike"] if p else None


def extract_expiration(symbol: str) -> date | None:
    """Extract the expiration date from an OCC symbol."""
    p = parse_occ(symbol)
    return p["expiration"] if p else None


def extract_option_type(symbol: str) -> str | None:
    """Return 'C' or 'P' from an OCC symbol."""
    p = parse_occ(symbol)
    return p["option_type"] if p else None


def dte_from_occ(symbol: str, as_of: date | None = None) -> int | None:
    """Calculate DTE from an OCC symbol. Uses today if as_of not given."""
    exp = extract_expiration(symbol)
    if exp is None:
        return None
    if as_of is None:
        as_of = date.today()
    return (exp - as_of).days


def is_put(symbol: str) -> bool:
    """True if the OCC symbol represents a put option."""
    return extract_option_type(symbol) == "P"


def is_call(symbol: str) -> bool:
    """True if the OCC symbol represents a call option."""
    return extract_option_type(symbol) == "C"


def validate_occ(symbol: str) -> bool:
    """Return True if symbol matches OCC format."""
    return parse_occ(symbol) is not None
