from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from django.test import Client


@dataclass
class CredentialRedactingProxy:
    """In-process representative edge that persists only a route template."""

    log_path: Path

    def request(self, path, *, method="get", data=None):
        parsed = urlsplit(path)
        segments = parsed.path.split("/")
        persisted_path = parsed.path
        if len(segments) == 4 and segments[1] == "invitations":
            persisted_path = "/invitations/<redacted>/<redacted>"
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{method.upper()} {persisted_path}\n")
        client = Client(raise_request_exception=False)
        return getattr(client, method.lower())(path, data=data or {})
