import io
import logging

import pytest

from config.logging import InvitationCredentialFilter, JsonFormatter
from tests.support.credential_redacting_proxy import CredentialRedactingProxy
from tests.test_invitation_acceptance import acceptance_fixture

pytestmark = pytest.mark.django_db(transaction=True)


def test_representative_edge_persists_only_redacted_route(tmp_path):
    _, _, _, invitation, token = acceptance_fixture()
    selector = str(invitation.id)
    path = f"/invitations/{selector}/{token}?canary=query"
    proxy_log = tmp_path / "edge.log"
    proxy = CredentialRedactingProxy(proxy_log)
    assert proxy.request(path).status_code == 200
    assert proxy.request(f"/invitations/{selector}/{'x' * 43}").status_code == 404
    persisted = proxy_log.read_bytes()
    assert b"/invitations/<redacted>/<redacted>" in persisted
    for forbidden in (f"/{selector}/".encode(), token.encode(), b"canary", b"query"):
        assert forbidden not in persisted


def test_application_formatter_redacts_credential_path():
    _, _, _, invitation, token = acceptance_fixture()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(InvitationCredentialFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("sb06-redaction-proof")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.WARNING)
    logger.warning("Failure at /invitations/%s/%s", invitation.id, token)
    rendered = stream.getvalue()
    assert "/invitations/<redacted>/<redacted>" in rendered
    assert token not in rendered and f"/{invitation.id}/" not in rendered


def test_representative_edge_redacts_application_error_request(tmp_path, monkeypatch):
    _, _, _, invitation, token = acceptance_fixture()
    monkeypatch.setattr(
        "identity.views.get_public_invitation_envelope",
        lambda selector, raw_token: (_ for _ in ()).throw(RuntimeError("synthetic")),
    )
    proxy_log = tmp_path / "edge-error.log"
    response = CredentialRedactingProxy(proxy_log).request(
        f"/invitations/{invitation.id}/{token}?private=yes"
    )
    assert response.status_code == 500
    persisted = proxy_log.read_text(encoding="utf-8")
    assert persisted == "GET /invitations/<redacted>/<redacted>\n"
    assert token not in persisted and "private" not in persisted
