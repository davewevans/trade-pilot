"""Shadow-execution follow-up capture job.

Scans shadow_executions for rows with pending tXX captures past their due
time, fetches NBBO (ORATS → Alpaca fallback), classifies, updates the row.

Cadence: 1 minute (scheduler library minimum). This means the +30s capture
lands at +30s to +90s in practice. Acceptable for v1; documented as a
known limitation of the measurement.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET_ZONE = ZoneInfo("America/New_York")
_ASYNC_LABELS = ("t30s", "t2m", "t15m", "eod")
_MAX_ATTEMPTS = 5


def run() -> None:
    """Scan pending shadow_execution rows and capture NBBO for due snapshots."""
    try:
        _run_inner()
    except Exception:
        logger.exception("shadow_capture job failed (non-fatal)")


def _run_inner() -> None:
    from config import settings
    if not settings.SHADOW_EXECUTION_ENABLED:
        return

    from scheduler import is_weekday
    if not is_weekday():
        return

    now_et = datetime.now(_ET_ZONE)
    # Operate 09:30–16:30 ET (extend 30 min past close for EOD captures)
    if not (9 * 60 + 30 <= now_et.hour * 60 + now_et.minute <= 16 * 60 + 30):
        return

    from database.db import Database
    from database.repositories.shadow_execution import ShadowExecutionRepository
    from data.shadow_execution import classify_fillability, fetch_leg_quotes_for_capture

    db = Database(path=str(settings.DATABASE_PATH))
    repo = ShadowExecutionRepository(db.get_connection())

    now_utc = datetime.now(timezone.utc)
    one_trading_day = 86400  # seconds

    total_scanned = 0
    total_updated = 0
    total_completed = 0
    total_failed_permanent = 0

    try:
        for label in _ASYNC_LABELS:
            due_rows = repo.get_due(label, limit=100)
            total_scanned += len(due_rows)

            for row in due_rows:
                exec_id = row["id"]
                legs = row.get("legs", [])
                is_credit = bool(row["is_credit"])
                net_limit_abs = row["net_limit_abs"]
                current_attempts = row.get(f"{label}_attempts", 0)

                submitted_at_str = row["submitted_at"]
                try:
                    submitted_dt = datetime.fromisoformat(submitted_at_str).replace(
                        tzinfo=timezone.utc
                    )
                except (ValueError, TypeError):
                    submitted_dt = now_utc

                # Check permanent failure conditions
                past_trading_day = (now_utc - submitted_dt).total_seconds() > one_trading_day
                max_attempts_hit = current_attempts >= _MAX_ATTEMPTS

                if past_trading_day or max_attempts_hit:
                    # EOD special: try last_trade before giving up
                    eod_source_detail = None
                    if label == "eod":
                        eod_source_detail = "unavailable"

                    repo.update_capture(
                        exec_id, label,
                        net_bid=None, net_mid=None, net_ask=None,
                        class_value=None,
                        legs_data=[
                            {"contract_symbol": leg["contract_symbol"],
                             "bid": None, "ask": None, "mid": None, "source": "unavailable"}
                            for leg in legs
                        ],
                        status="failed_permanent",
                        attempts_increment=1,
                        eod_source_detail=eod_source_detail,
                    )
                    repo.recompute_completed(exec_id)
                    total_failed_permanent += 1
                    total_updated += 1
                    continue

                # Attempt NBBO capture
                try:
                    quotes = fetch_leg_quotes_for_capture(legs)

                    # For EOD after 16:00 ET, try last_trade if quote unavailable
                    eod_source_detail = None
                    if label == "eod":
                        all_unavail = all(
                            quotes.get(leg["contract_symbol"], {}).get("source") == "unavailable"
                            for leg in legs
                        )
                        if all_unavail:
                            eod_source_detail = "unavailable"
                            # Still fall through to failed_permanent for EOD when unavailable
                            repo.update_capture(
                                exec_id, label,
                                net_bid=None, net_mid=None, net_ask=None,
                                class_value="data_unavailable",
                                legs_data=[
                                    {"contract_symbol": leg["contract_symbol"],
                                     "bid": None, "ask": None, "mid": None, "source": "unavailable"}
                                    for leg in legs
                                ],
                                status="failed_permanent",
                                attempts_increment=1,
                                eod_source_detail=eod_source_detail,
                            )
                            repo.recompute_completed(exec_id)
                            total_failed_permanent += 1
                            total_updated += 1
                            continue
                        else:
                            eod_source_detail = "quote"

                    from data.shadow_execution import _compute_net_nbbo
                    net_bid, net_mid, net_ask = _compute_net_nbbo(legs, quotes)
                    classification = classify_fillability(
                        is_credit=is_credit,
                        limit_magnitude=net_limit_abs,
                        net_bid=net_bid,
                        net_mid=net_mid,
                        net_ask=net_ask,
                    )

                    leg_updates = []
                    for leg in legs:
                        sym = leg["contract_symbol"]
                        q = quotes.get(sym, {})
                        leg_updates.append({
                            "contract_symbol": sym,
                            "bid": q.get("bid"),
                            "ask": q.get("ask"),
                            "mid": q.get("mid"),
                            "source": q.get("source", "unavailable"),
                        })

                    repo.update_capture(
                        exec_id, label,
                        net_bid=net_bid, net_mid=net_mid, net_ask=net_ask,
                        class_value=classification,
                        legs_data=leg_updates,
                        status="captured",
                        attempts_increment=1,
                        eod_source_detail=eod_source_detail,
                    )
                    repo.recompute_completed(exec_id)
                    total_updated += 1

                    # Check if completed flipped
                    updated = db.get_connection().execute(
                        "SELECT completed FROM shadow_executions WHERE id = ?", (exec_id,)
                    ).fetchone()
                    if updated and updated[0]:
                        total_completed += 1

                except Exception:
                    logger.debug(
                        "shadow_capture: NBBO fetch failed for row %d label %s",
                        exec_id, label, exc_info=True,
                    )
                    new_attempts = current_attempts + 1
                    status = "failed_permanent" if new_attempts >= _MAX_ATTEMPTS else "failed_retryable"
                    eod_detail = "unavailable" if label == "eod" and status == "failed_permanent" else None
                    repo.update_capture(
                        exec_id, label,
                        net_bid=None, net_mid=None, net_ask=None,
                        class_value=None,
                        legs_data=[
                            {"contract_symbol": leg["contract_symbol"],
                             "bid": None, "ask": None, "mid": None, "source": "unavailable"}
                            for leg in legs
                        ],
                        status=status,
                        attempts_increment=1,
                        eod_source_detail=eod_detail,
                    )
                    if status == "failed_permanent":
                        repo.recompute_completed(exec_id)
                        total_failed_permanent += 1
                    total_updated += 1

        logger.info(
            "shadow_capture: scanned=%d updated=%d completed=%d failed_permanent=%d",
            total_scanned, total_updated, total_completed, total_failed_permanent,
        )
    finally:
        db.close()
