from fastapi.testclient import TestClient

from finsight.api.main import app

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "finsight-api",
    }


def test_readiness_endpoint_when_database_is_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "finsight.api.main.check_database_connection",
        lambda: True,
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "connected",
    }


def test_readiness_endpoint_when_database_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "finsight.api.main.check_database_connection",
        lambda: False,
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable."}
