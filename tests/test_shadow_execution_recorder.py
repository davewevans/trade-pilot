"""Integration tests for data.shadow_execution.record_submission."""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from database.db import Database
from database.repositories.shadow_execution import ShadowExecutionRepository


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db(tmp_path) -> Database:
    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    return db


def _single_leg(symbol="SPY240119P00500000", intent="sell_to_open", side="sell"):
    return [
        {
            "contract_symbol": symbol,
            "leg_role": "short_put",
            "side": side,
            "position_intent": intent,
        }
    ]


def _multi_leg():
    return [
        {
            "contract_symbol": "SPY240119P00490000",
            "leg_role": "short_put",
            "side": "sell",
            "position_intent": "sell_to_open",
        },
        {
            "contract_symbol": "SPY240119P00480000",
            "leg_role": "long_put",
            "side": "buy",
            "position_intent": "buy_to_open",
        },
    ]


def _mock_quotes_available(monkeypatch):
    """Patch _fetch_leg_quotes to return valid NBBO for all legs."""
    def _fake_fetch(legs):
        return {
            leg["contract_symbol"]: {
                "bid": 1.00, "ask": 1.50, "mid": 1.25, "source": "orats"
            }
            for leg in legs
        }
    monkeypatch.setattr(
        "data.shadow_execution._fetch_leg_quotes", _fake_fetch
    )


def _mock_quotes_unavailable(monkeypatch):
    """Patch _fetch_leg_quotes to return all-unavailable."""
    def _fake_fetch(legs):
        return {
            leg["contract_symbol"]: {
                "bid": None, "ask": None, "mid": None, "source": "unavailable"
            }
            for leg in legs
        }
    monkeypatch.setattr(
        "data.shadow_execution._fetch_leg_quotes", _fake_fetch
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestFlagOff:
    def test_returns_none_when_disabled(self, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", False)
        from data.shadow_execution import record_submission
        result = record_submission(
            strategy_type="bull_put_spread",
            action="sell_put",
            legs=_single_leg(),
            net_limit_price=-1.50,
            alpaca_order_id="abc123",
        )
        assert result is None


class TestSellPutSingleLeg:
    def test_inserts_parent_row(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")
        db = _make_db(tmp_path)
        _mock_quotes_available(monkeypatch)

        from data.shadow_execution import record_submission
        exec_id = record_submission(
            strategy_type="sell_put",
            action="sell_put",
            legs=_single_leg(),
            net_limit_price=-1.50,
            alpaca_order_id="order1",
        )

        assert exec_id is not None
        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM shadow_executions WHERE id = ?", (exec_id,)
        ).fetchone()
        d = dict(row)
        assert d["strategy_type"] == "sell_put"
        assert d["action"] == "sell_put"
        assert d["is_credit"] == 1
        assert abs(d["net_limit_abs"] - 1.50) < 0.001
        assert d["t0_status"] == "captured"
        assert d["t0_class"] is not None
        assert d["completed"] == 0
        db.close()

    def test_inserts_leg_row(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")
        db = _make_db(tmp_path)
        _mock_quotes_available(monkeypatch)

        from data.shadow_execution import record_submission
        exec_id = record_submission(
            strategy_type="sell_put",
            action="sell_put",
            legs=_single_leg(),
            net_limit_price=-1.50,
            alpaca_order_id=None,
        )

        conn = db.get_connection()
        legs = conn.execute(
            "SELECT * FROM shadow_execution_legs WHERE shadow_exec_id = ?", (exec_id,)
        ).fetchall()
        assert len(legs) == 1
        leg = dict(legs[0])
        assert leg["contract_symbol"] == "SPY240119P00500000"
        assert leg["leg_role"] == "short_put"
        assert leg["t0_bid"] == pytest.approx(1.00)
        assert leg["t0_source"] == "orats"
        db.close()


class TestCloseShort:
    def test_close_is_debit(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")
        db = _make_db(tmp_path)
        _mock_quotes_available(monkeypatch)

        from data.shadow_execution import record_submission
        exec_id = record_submission(
            strategy_type="sell_put",
            action="close_short",
            legs=_single_leg(intent="buy_to_close", side="buy"),
            net_limit_price=+0.50,
            alpaca_order_id=None,
        )

        conn = db.get_connection()
        row = dict(conn.execute(
            "SELECT * FROM shadow_executions WHERE id = ?", (exec_id,)
        ).fetchone())
        assert row["is_credit"] == 0
        assert abs(row["net_limit_abs"] - 0.50) < 0.001
        db.close()


class TestMultiLeg:
    def test_bull_put_spread_two_legs(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")
        db = _make_db(tmp_path)
        _mock_quotes_available(monkeypatch)

        from data.shadow_execution import record_submission
        exec_id = record_submission(
            strategy_type="bull_put_spread",
            action="sell_put",
            legs=_multi_leg(),
            net_limit_price=-0.75,
            alpaca_order_id="multi1",
        )

        conn = db.get_connection()
        legs = conn.execute(
            "SELECT * FROM shadow_execution_legs WHERE shadow_exec_id = ?", (exec_id,)
        ).fetchall()
        assert len(legs) == 2
        assert dict(conn.execute(
            "SELECT order_kind FROM shadow_executions WHERE id = ?", (exec_id,)
        ).fetchone())["order_kind"] == "multi_leg"
        db.close()


class TestOratsUnavailable:
    def test_falls_back_to_unavailable(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")
        db = _make_db(tmp_path)
        _mock_quotes_unavailable(monkeypatch)

        from data.shadow_execution import record_submission
        exec_id = record_submission(
            strategy_type="sell_put",
            action="sell_put",
            legs=_single_leg(),
            net_limit_price=-1.50,
            alpaca_order_id=None,
        )

        conn = db.get_connection()
        row = dict(conn.execute(
            "SELECT * FROM shadow_executions WHERE id = ?", (exec_id,)
        ).fetchone())
        assert row["t0_status"] == "failed_permanent"
        assert row["t0_class"] == "data_unavailable"
        db.close()


class TestExceptionHandling:
    def test_fetch_exception_returns_none(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")
        _make_db(tmp_path)

        def _explode(legs):
            raise RuntimeError("network error")
        monkeypatch.setattr("data.shadow_execution._fetch_leg_quotes", _explode)

        from data.shadow_execution import record_submission
        result = record_submission(
            strategy_type="sell_put",
            action="sell_put",
            legs=_single_leg(),
            net_limit_price=-1.50,
            alpaca_order_id=None,
        )
        # Must not raise; must return None gracefully
        assert result is None
