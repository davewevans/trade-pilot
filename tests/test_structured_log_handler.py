"""Tests for JSONLStructuredHandler in utils/structured_log_handler."""

import json
import logging
from pathlib import Path

from utils.structured_log_handler import JSONLStructuredHandler, install_structured_log_handler


def _fresh_handler(tmp_path) -> JSONLStructuredHandler:
    """Create a handler and ensure the root logger has no prior instance."""
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, JSONLStructuredHandler)]
    return install_structured_log_handler(str(tmp_path))


def test_handler_writes_warning_with_extra(tmp_path):
    handler = _fresh_handler(tmp_path)
    logger = logging.getLogger("test.structured.warning")
    logger.warning("test message", extra={"symbol": "AAPL", "value": 42})
    handler.flush()
    files = list(Path(tmp_path).glob("*.jsonl"))
    assert len(files) == 1
    lines = [l for l in files[0].read_text().strip().split("\n") if l]
    record = json.loads(lines[-1])
    assert record["level"] == "WARNING"
    assert record["logger"] == "test.structured.warning"
    assert record["message"] == "test message"
    assert record["extra"]["symbol"] == "AAPL"
    assert record["extra"]["value"] == 42


def test_handler_captures_traceback(tmp_path):
    handler = _fresh_handler(tmp_path)
    logger = logging.getLogger("test.structured.exc")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("caught it")
    handler.flush()
    files = list(Path(tmp_path).glob("*.jsonl"))
    assert files
    lines = [l for l in files[0].read_text().strip().split("\n") if l]
    record = json.loads(lines[-1])
    assert record["traceback"] is not None
    assert "ValueError: boom" in record["traceback"]


def test_handler_ignores_info(tmp_path):
    handler = _fresh_handler(tmp_path)
    logger = logging.getLogger("test.structured.info")
    logger.info("should not appear")
    handler.flush()
    files = list(Path(tmp_path).glob("*.jsonl"))
    if files:
        content = files[0].read_text().strip()
        # If a file exists, any lines in it must not be from our info call
        for line in content.split("\n"):
            if not line:
                continue
            rec = json.loads(line)
            assert rec["message"] != "should not appear"


def test_install_is_idempotent(tmp_path):
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, JSONLStructuredHandler)]
    h1 = install_structured_log_handler(str(tmp_path))
    h2 = install_structured_log_handler(str(tmp_path))
    assert h1 is h2
    structured = [h for h in root.handlers if isinstance(h, JSONLStructuredHandler)]
    assert len(structured) == 1


def test_log_dir_created_if_missing(tmp_path):
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, JSONLStructuredHandler)]
    new_dir = tmp_path / "nested" / "logs"
    assert not new_dir.exists()
    install_structured_log_handler(str(new_dir))
    assert new_dir.exists()
