"""Smoke test: the app boots and the health route returns 200.

The root route ``/`` became the authenticated landing in F2 (anonymous requests
redirect to login); its behaviour is covered in test_auth.py.
"""


def test_health_route_returns_200(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
