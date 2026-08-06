"""Allowlist-based JSON logging for foundation diagnostics."""

import json
import logging
import re
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format a safe, stable subset of a log record as JSON."""

    def format(self, record):
        message = redact_invitation_credentials(record.getMessage())
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        for field in ("operation", "result_code"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception_class"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


INVITATION_PATH = re.compile(r"/invitations/[^/?\s]+/[^/?\s]+")


def redact_invitation_credentials(value):
    return INVITATION_PATH.sub("/invitations/<redacted>/<redacted>", str(value))


class InvitationCredentialFilter(logging.Filter):
    def filter(self, record):
        rendered = record.getMessage()
        redacted = redact_invitation_credentials(rendered)
        if rendered != redacted:
            record.msg = redacted
            record.args = ()
        return True
