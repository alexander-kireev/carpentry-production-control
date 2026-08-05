import pytest
from django.contrib.staticfiles import finders


def test_root_renders_base_include_and_static_reference(client):
    response = client.get("/", HTTP_HOST="localhost")

    assert response.status_code == 200
    assert [template.name for template in response.templates] == [
        "base.html",
        "includes/foundation_status.html",
    ]
    assert b"data-foundation-status" in response.content
    assert b"/static/css/foundation.css" in response.content


def test_foundation_stylesheet_is_discoverable():
    assert finders.find("css/foundation.css") is not None


@pytest.mark.django_db
def test_health_is_minimal_and_query_free(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/health/", HTTP_HOST="localhost")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["Content-Type"] == "application/json"
