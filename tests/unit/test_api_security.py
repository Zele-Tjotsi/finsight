from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from finsight.api import main

client = TestClient(main.app)


def test_production_rejects_missing_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(app_env="production", api_key="correct-secret"),
    )

    response = client.get("/analytics/overview")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key."}


def test_production_rejects_incorrect_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(app_env="production", api_key="correct-secret"),
    )

    response = client.get(
        "/analytics/overview",
        headers={"X-API-Key": "incorrect-secret"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key."}


def test_production_rejects_unconfigured_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(app_env="production", api_key=None),
    )

    response = client.get("/analytics/overview")

    assert response.status_code == 503
    assert response.json() == {"detail": "API authentication is not configured."}


def test_production_accepts_correct_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(app_env="production", api_key="correct-secret"),
    )

    response = client.get(
        f"/imports/{uuid4()}",
        headers={"X-API-Key": "correct-secret"},
    )

    # Authentication succeeded; the requested batch simply doesn't exist.
    assert response.status_code == 404
    assert response.json() == {"detail": "Import batch not found."}


def test_health_endpoint_remains_public_in_production(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(app_env="production", api_key="correct-secret"),
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
