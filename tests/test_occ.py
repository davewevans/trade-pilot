"""Tests for utils.occ."""

from datetime import date

from utils.occ import (
    dte_from_occ,
    extract_expiration,
    extract_option_type,
    extract_root,
    extract_strike,
    is_call,
    is_put,
    parse_occ,
    validate_occ,
)


def test_parse_valid_multichar_root():
    p = parse_occ("AAPL260515P00260000")
    assert p["root"] == "AAPL"
    assert p["expiration"] == date(2026, 5, 15)
    assert p["option_type"] == "P"
    assert p["strike"] == 260.0
    assert p["expiration_str"] == "2026-05-15"


def test_parse_single_char_root():
    p = parse_occ("A260615P00120000")
    assert p["root"] == "A"
    assert p["strike"] == 120.0


def test_parse_six_char_letter_root():
    p = parse_occ("BRKBRK261218C01000000")
    assert p is not None
    assert p["root"] == "BRKBRK"


def test_rejects_digit_in_root():
    # "GOOGL2" has a digit — per our spec root is letters-only.
    assert parse_occ("GOOGL2261218C01000000") is None


def test_invalid_symbols():
    assert parse_occ("") is None
    assert parse_occ("AAPL") is None
    assert parse_occ("TOOLONG260515P00260000") is None  # 7-char root
    assert parse_occ("AAPL260515X00260000") is None  # bad type
    assert parse_occ("AAPL269999P00260000") is None  # bad date


def test_extract_helpers():
    sym = "SPY260502C00550000"
    assert extract_root(sym) == "SPY"
    assert extract_strike(sym) == 550.0
    assert extract_option_type(sym) == "C"
    assert extract_expiration(sym) == date(2026, 5, 2)
    assert is_call(sym)
    assert not is_put(sym)
    assert validate_occ(sym)


def test_dte():
    assert dte_from_occ("SPY260502C00550000", as_of=date(2026, 5, 1)) == 1
    assert dte_from_occ("SPY260502C00550000", as_of=date(2026, 5, 2)) == 0
    assert dte_from_occ("NOTVALID") is None


def test_is_put():
    assert is_put("AAPL260515P00260000")
    assert not is_put("AAPL260515C00260000")
