"""JSON log formatter for the api_calls structured log.

Produces one JSON object per line suitable for ``jq`` queries.
"""

import datetime
import json
import logging


class JsonFormatter(logging.Formatter):
    """Format a LogRecord as a single JSON line.

    Expected fields (supplied via ``logging.info("", extra={...})``)::

        api           str    e.g. 'orats_historical'
        endpoint      str    e.g. 'hist/summaries'
        symbol        str|None
        status        int|None
        cache_hit     bool
        duration_ms   int|None
        blocked_reason str|None
        pid           int
        job_name      str|None

    Any missing ``extra`` fields are emitted as ``null``.
    """

    _FIELDS = (
        "api",
        "endpoint",
        "symbol",
        "status",
        "cache_hit",
        "duration_ms",
        "blocked_reason",
        "pid",
        "job_name",
    )

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        obj: dict = {"ts": ts}
        for field in self._FIELDS:
            obj[field] = getattr(record, field, None)

        return json.dumps(obj, default=str)
