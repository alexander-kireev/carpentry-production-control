"""Allowlist-based JSON logging for foundation diagnostics."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format a safe, stable subset of a log record as JSON."""

    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("operation", "result_code"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception_class"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)
