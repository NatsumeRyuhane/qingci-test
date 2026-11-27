from fastapi.testclient import TestClient

from copilot import __version__
from copilot.api import create_app


def test_health() -> None:
    client = TestClient(create_app())
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_openapi_is_served_under_v1() -> None:
    client = TestClient(create_app())
    response = client.get("/v1/openapi.json")
    assert response.status_code == 200
    assert "/v1/health" in response.json()["paths"]
