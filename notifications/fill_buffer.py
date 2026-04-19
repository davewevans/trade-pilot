"""
Batched fill notifications with a 30-second rolling debounce.

Multiple fills arriving within 30 seconds of each other are bundled
into a single ntfy push. Only active when ``ALERT_FILLS=true`` in config.

Thread-safe via a module-level lock; uses ``threading.Timer`` so the
debounce reset doesn't block the scheduler loop.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_buffer: list[dict] = []
_timer: threading.Timer | None = None
_DEBOUNCE_SECS = 30


def _flush() -> None:
    global _buffer, _timer
    with _lock:
        fills = _buffer[:]
        _buffer = []
        _timer = None

    if not fills:
        return

    try:
        from notifications import notify

        if len(fills) == 1:
            f = fills[0]
            title = f"Fill: {f['action']} {f['symbol']}"
            msg = f"Filled at {f['fill_price']}"
            if f.get("limit_price") is not None:
                try:
                    slippage = float(f["fill_price"]) - float(f["limit_price"])
                    msg += f" (limit {f['limit_price']}, slip {slippage:+.2f})"
                except (TypeError, ValueError):
                    msg += f" (limit {f['limit_price']})"
        else:
            title = f"{len(fills)} fills"
            lines = []
            for f in fills:
                line = f"{f['action']} {f['symbol']} @ {f['fill_price']}"
                if f.get("limit_price") is not None:
                    try:
                        slippage = float(f["fill_price"]) - float(f["limit_price"])
                        line += f" (slip {slippage:+.2f})"
                    except (TypeError, ValueError):
                        pass
                lines.append(line)
            msg = "\n".join(lines)

        notify("high", title, msg, tags=["fill"])
    except Exception:
        logger.debug("fill_buffer flush failed", exc_info=True)


def record(action: str, symbol: str, fill_price, limit_price=None) -> None:
    """Add a fill to the buffer and reset the debounce timer."""
    global _timer

    entry = {
        "action": action,
        "symbol": symbol,
        "fill_price": fill_price,
        "limit_price": limit_price,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    with _lock:
        _buffer.append(entry)
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(_DEBOUNCE_SECS, _flush)
        _timer.daemon = True
        _timer.start()
