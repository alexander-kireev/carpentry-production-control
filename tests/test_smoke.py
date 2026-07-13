"""Smoke test: the app boots and the health/root route returns 200."""


def test_health_route_returns_200(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_route_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200
