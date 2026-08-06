import pytest
from django.conf import settings
from django.contrib.sessions.models import Session
from django.contrib.staticfiles import finders
from django.test import override_settings


def test_anonymous_root_redirects_to_login(client):
    response = client.get("/", HTTP_HOST="localhost")

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"


def test_public_forms_use_shared_stylesheet(client):
    response = client.get("/register", HTTP_HOST="localhost")
    assert response.status_code == 200
    assert b"/static/css/foundation.css" in response.content
    assert b"Notifications" not in response.content
    assert b"forgot" not in response.content.lower()
    assert b"data-submit-once" in response.content
    assert b"data-submit-status" in response.content
    assert b'autocomplete="bday"' in response.content
    assert b'autocomplete="one-time-code"' in response.content
    assert b'aria-describedby="code-help"' in response.content
    assert b'id="code-help"' in response.content


def test_login_has_canonical_in_flight_guard(client):
    response = client.get("/login", HTTP_HOST="localhost")
    assert b"data-submit-once" in response.content
    assert b"data-submit-status" in response.content
    assert b"Signing in" in response.content


@pytest.mark.django_db(transaction=True)
@override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="http-activation-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="http-independent-hmac-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=1,
)
def test_registration_http_commits_then_authenticates(client):
    page = client.get("/register", REMOTE_ADDR="192.0.2.80")
    nonce = page.context["form"].initial["submission_nonce"]
    response = client.post(
        "/register",
        {
            "submission_nonce": nonce,
            "first_name": "Grace",
            "last_name": "Hopper",
            "date_of_birth": "1990-01-01",
            "email": "grace@example.test",
            "password": "Compiler-Workshop-483!",
            "password_confirmation": "Compiler-Workshop-483!",
            "activation_code": "http-activation-code",
        },
        REMOTE_ADDR="192.0.2.80",
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/onboarding/workshop"
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_login_failure_is_generic_and_ignores_next(client):
    response = client.post(
        "/login?next=/operations",
        {"email": "missing@example.test", "password": "wrong"},
    )
    assert response.status_code == 400
    assert b"email address or password was not recognised" in response.content
    assert b"missing@example.test" not in response.content
    assert "Retry-After" not in response.headers


@pytest.mark.django_db(transaction=True)
@override_settings(
    ADMIN_REGISTRATION_ACTIVATION_CODE="session-failure-code",
    ADMIN_REGISTRATION_IP_HMAC_KEY="session-failure-independent-key",
    ADMIN_REGISTRATION_IP_HMAC_KEY_VERSION=1,
)
def test_post_commit_session_failure_keeps_account_for_login(client, monkeypatch):
    from identity.results import CommandResult, ResultCode

    page = client.get("/register", REMOTE_ADDR="192.0.2.81")
    nonce = page.context["form"].initial["submission_nonce"]
    monkeypatch.setattr(
        "identity.views.establish_session",
        lambda request, user: CommandResult(ResultCode.SESSION_FAILED, user=user),
    )
    response = client.post(
        "/register",
        {
            "submission_nonce": nonce,
            "first_name": "Session",
            "last_name": "Recovery",
            "date_of_birth": "1990-01-01",
            "email": "recovery@example.test",
            "password": "Recovery-Password-483!",
            "password_confirmation": "Recovery-Password-483!",
            "activation_code": "session-failure-code",
        },
        REMOTE_ADDR="192.0.2.81",
    )
    assert response.status_code == 503
    assert b"account was created" in response.content
    assert "_auth_user_id" not in client.session

    monkeypatch.undo()
    login_response = client.post(
        "/login",
        {"email": "RECOVERY@EXAMPLE.TEST", "password": "Recovery-Password-483!"},
    )
    assert login_response.headers["Location"] == "/onboarding/workshop"


def test_foundation_stylesheet_is_discoverable():
    assert finders.find("css/foundation.css") is not None


def test_onboarding_routes_have_no_trailing_slash_alias(client):
    for path in (
        "/onboarding/workshop/",
        "/onboarding/manager/",
        "/onboarding/",
        "/onboarding/holding/",
        "/dashboard/",
    ):
        assert client.get(path).status_code in {301, 302, 404}


@pytest.mark.django_db
def test_health_is_minimal_and_query_free(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/health/", HTTP_HOST="localhost")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["Content-Type"] == "application/json"


@pytest.mark.django_db
def test_health_bypasses_valid_session_without_query_or_mutation(
    client, django_assert_num_queries
):
    session = client.session
    session["health_probe"] = "preserve"
    session.save()
    session_key = session.session_key
    before = Session.objects.values_list("session_data", "expire_date").get(
        session_key=session_key
    )
    cookie_before = client.cookies[settings.SESSION_COOKIE_NAME].value

    with django_assert_num_queries(0):
        response = client.get("/health/", HTTP_HOST="localhost")

    after = Session.objects.values_list("session_data", "expire_date").get(
        session_key=session_key
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.cookies == {}
    assert client.cookies[settings.SESSION_COOKIE_NAME].value == cookie_before
    assert after == before


@pytest.mark.django_db
def test_health_bypasses_stale_session_without_query_or_mutation(
    client, django_assert_num_queries
):
    stale_key = "s" * 32
    client.cookies[settings.SESSION_COOKIE_NAME] = stale_key

    with django_assert_num_queries(0):
        response = client.get("/health/", HTTP_HOST="localhost")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.cookies == {}
    assert client.cookies[settings.SESSION_COOKIE_NAME].value == stale_key
    assert not Session.objects.filter(session_key=stale_key).exists()
