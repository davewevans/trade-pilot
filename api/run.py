"""Start the trade-pilot API server.

Usage::

    python api/run.py              # localhost:8000
    python api/run.py --port 3001  # custom port
"""

import argparse
import logging
import os
import sys

# Ensure the project root is on the path when run directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from config import settings as _settings  # noqa: E402
if _settings.STRUCTURED_LOG_CAPTURE_ENABLED:
    from utils.structured_log_handler import install_structured_log_handler
    install_structured_log_handler(
        log_dir=_settings.STRUCTURED_LOG_DIR,
        backup_count=_settings.STRUCTURED_LOG_RETENTION_DAYS,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="trade-pilot API server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
