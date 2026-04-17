"""Live integration tests for AlpacaBroker against the real Alpaca paper API.

Run from the project root:
    python scripts/test_alpaca_live.py

Each check prints PASS / FAIL with details.  The script exits 1 if any
check fails so it can be used in CI or as a pre-flight before the 10am run.

Checks:
  1. SDK version guard — OptionsSnapshot fields match what we expect
  2. get_option_chain_with_greeks — no HTTP error, correct response shape
  3. get_option_snapshots (small batch, <50 symbols)
  4. get_option_snapshots (large batch, >50 symbols — exercises the 50-symbol
     batching logic that caused previous HTTP 400 errors)
  5. Field-shape validation on each snapshot dict returned by get_option_snapshots
  6. No "Failed to parse snapshot" warnings in broker logs
"""

import logging
import sys
import os
from datetime import date, timedelta

# Allow running from scripts/ or from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── capture broker WARNING logs so we can assert none fired ─
_parse_warnings: list[str] = []

class _WarnCapture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            _parse_warnings.append(record.getMessage())

_broker_logger = logging.getLogger("brokers.alpaca_broker")
_broker_logger.setLevel(logging.DEBUG)
_broker_logger.addHandler(_WarnCapture())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("test-alpaca-live")

# ── result tracking ──────────────────────────────────────────

_results: list[tuple[str, bool, str]] = []   # (name, passed, detail)

def check(name: str, passed: bool, detail: str = "") -> None:
    _results.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    log.info("[%s] %s%s", status, name, f" — {detail}" if detail else "")


def _expiry_window() -> tuple[str, str]:
    """Return (gte, lte) spanning the next 30 days."""
    today = date.today()
    return today.strftime("%Y-%m-%d"), (today + timedelta(days=30)).strftime("%Y-%m-%d")


# ── expected snapshot keys ───────────────────────────────────

_EXPECTED_SNAPSHOT_KEYS = {
    "bid", "ask", "mid", "delta", "theta", "vega", "gamma",
    "iv", "open_interest", "last_trade_size",
}

_EXPECTED_SNAPSHOT_MODEL_FIELDS = {
    "symbol", "latest_trade", "latest_quote", "implied_volatility", "greeks",
}


# ── checks ───────────────────────────────────────────────────

def check_sdk_model_fields() -> None:
    """Verify OptionsSnapshot Pydantic model still has the fields we rely on."""
    try:
        from alpaca.data.models.snapshots import OptionsSnapshot
        actual = set(OptionsSnapshot.model_fields.keys())
        missing = _EXPECTED_SNAPSHOT_MODEL_FIELDS - actual
        unexpected = actual - _EXPECTED_SNAPSHOT_MODEL_FIELDS
        if missing:
            check(
                "SDK: OptionsSnapshot fields",
                False,
                f"Fields we depend on are MISSING from SDK model: {missing}. "
                "The alpaca-py package may have been upgraded — review broker code.",
            )
        elif unexpected:
            # Extra fields are fine — just warn so we know about them.
            check(
                "SDK: OptionsSnapshot fields",
                True,
                f"New fields in SDK model (not a problem): {unexpected}",
            )
        else:
            check("SDK: OptionsSnapshot fields", True, f"fields={sorted(actual)}")
    except Exception as exc:
        check("SDK: OptionsSnapshot fields", False, str(exc))


def check_option_chain(broker) -> list[dict]:
    """Call get_option_chain_with_greeks and validate response shape."""
    gte, lte = _expiry_window()
    contracts: list[dict] = []
    try:
        contracts = broker.get_option_chain_with_greeks(
            underlying_symbol="SPY",
            expiration_date_gte=gte,
            expiration_date_lte=lte,
        )
        if not contracts:
            check("get_option_chain_with_greeks", False, "returned empty list — no SPY contracts in window")
            return []

        # Shape validation
        required_keys = {"symbol", "strike_price", "expiration_date", "type"}
        sample = contracts[0]
        missing_keys = required_keys - set(sample.keys())
        if missing_keys:
            check(
                "get_option_chain_with_greeks",
                False,
                f"Response dict missing keys: {missing_keys}",
            )
            return contracts

        # Check types on first contract
        assert isinstance(sample["symbol"], str) and len(sample["symbol"]) > 5, "symbol is not a valid string"
        assert isinstance(sample["strike_price"], (int, float)), "strike_price not numeric"

        check(
            "get_option_chain_with_greeks",
            True,
            f"contracts={len(contracts)}, sample_symbol={sample['symbol']}",
        )
    except AssertionError as exc:
        check("get_option_chain_with_greeks", False, f"Shape assertion: {exc}")
    except Exception as exc:
        check("get_option_chain_with_greeks", False, str(exc))

    return contracts


def _validate_snapshot_dict(sym: str, data: dict) -> list[str]:
    """Return a list of error strings (empty = all good)."""
    errors: list[str] = []

    # All expected keys present
    missing = _EXPECTED_SNAPSHOT_KEYS - set(data.keys())
    if missing:
        errors.append(f"{sym}: missing keys {missing}")

    # No extra keys (would indicate a refactor mismatch)
    extra = set(data.keys()) - _EXPECTED_SNAPSHOT_KEYS
    if extra:
        errors.append(f"{sym}: unexpected extra keys {extra}")

    # Numeric-or-None fields
    for field in ("bid", "ask", "mid", "delta", "theta", "vega", "gamma", "iv"):
        val = data.get(field)
        if val is not None and not isinstance(val, (int, float)):
            errors.append(f"{sym}.{field}: expected float|None, got {type(val).__name__}={val!r}")

    # mid = (bid+ask)/2 when both are present
    bid, ask, mid = data.get("bid"), data.get("ask"), data.get("mid")
    if bid is not None and ask is not None and mid is not None:
        expected_mid = round((bid + ask) / 2, 4)
        if abs(mid - expected_mid) > 0.0001:
            errors.append(f"{sym}.mid: expected {expected_mid}, got {mid}")

    # last_trade_size: int or None
    lts = data.get("last_trade_size")
    if lts is not None and not isinstance(lts, int):
        errors.append(f"{sym}.last_trade_size: expected int|None, got {type(lts).__name__}={lts!r}")

    return errors


def check_snapshots_small(broker, symbols: list[str]) -> None:
    """Fetch snapshots for a small batch (<50) and validate each dict."""
    batch = symbols[:5]
    if not batch:
        check("get_option_snapshots (small batch)", False, "no symbols available from chain")
        return

    try:
        result = broker.get_option_snapshots(batch)
        if not result:
            check(
                "get_option_snapshots (small batch)",
                False,
                f"returned empty dict for {batch}",
            )
            return

        errors: list[str] = []
        for sym, data in result.items():
            errors.extend(_validate_snapshot_dict(sym, data))

        check(
            "get_option_snapshots (small batch)",
            not errors,
            f"fetched={len(result)}/{len(batch)} symbols" + (f"; errors: {errors}" if errors else ""),
        )
    except Exception as exc:
        check("get_option_snapshots (small batch)", False, str(exc))


def check_snapshots_large(broker, symbols: list[str]) -> None:
    """Fetch >50 symbols — exercises the batch-splitting logic."""
    # Need at least 51 symbols.  If the chain doesn't have that many, skip
    # with an info message rather than a false failure.
    if len(symbols) < 51:
        check(
            "get_option_snapshots (large batch >50)",
            True,
            f"SKIPPED — only {len(symbols)} contracts in window, need 51+ to exercise batching",
        )
        return

    # Use up to 110 symbols so we always get 3 batches if enough are available.
    batch = symbols[:110]
    try:
        result = broker.get_option_snapshots(batch)
        if not result:
            check(
                "get_option_snapshots (large batch >50)",
                False,
                "returned empty dict — possible HTTP 400 or all batches failed",
            )
            return

        errors: list[str] = []
        for sym, data in result.items():
            errors.extend(_validate_snapshot_dict(sym, data))

        check(
            "get_option_snapshots (large batch >50)",
            not errors,
            f"fetched={len(result)}/{len(batch)} symbols in {(len(batch) + 49) // 50} batches"
            + (f"; shape errors: {errors[:5]}" if errors else ""),
        )
    except Exception as exc:
        check("get_option_snapshots (large batch >50)", False, str(exc))


def check_no_parse_warnings() -> None:
    """Fail if any 'Failed to parse snapshot' warnings were emitted."""
    parse_failures = [m for m in _parse_warnings if "Failed to parse snapshot" in m]
    check(
        "No 'Failed to parse snapshot' warnings",
        not parse_failures,
        "; ".join(parse_failures) if parse_failures else "clean",
    )


# ── main ─────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 60)
    log.info("  Alpaca live integration tests")
    log.info("=" * 60)

    # 1. SDK guard — before instantiating broker
    check_sdk_model_fields()

    # Instantiate broker (reads .env credentials)
    try:
        from brokers.alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
    except Exception as exc:
        log.error("Could not instantiate AlpacaBroker: %s", exc)
        sys.exit(1)

    # 2. Option chain
    contracts = check_option_chain(broker)
    symbols = [c["symbol"] for c in contracts]

    # 3. Small snapshot batch
    check_snapshots_small(broker, symbols)

    # 4. Large snapshot batch (>50)
    check_snapshots_large(broker, symbols)

    # 5. Parse-warning check (runs after all snapshot calls)
    check_no_parse_warnings()

    # ── summary ─────────────────────────────────────────────
    log.info("=" * 60)
    log.info("  Results")
    log.info("=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    for name, ok, detail in _results:
        status = "PASS" if ok else "FAIL"
        log.info("  [%s] %s%s", status, name, f" — {detail}" if detail else "")
    log.info("")
    log.info("  %d passed, %d failed", passed, failed)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
