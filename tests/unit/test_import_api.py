from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from finsight.api import main
from finsight.ingestion.service import CsvUploadError

client = TestClient(main.app)


def test_upload_csv_returns_import_result(monkeypatch) -> None:
    expected = {
        "batch_id": str(uuid4()),
        "filename": "transactions.csv",
        "status": "COMPLETED",
        "total_rows": 1,
        "accepted_rows": 1,
        "rejected_rows": 0,
        "duplicate_rows": 0,
        "issues": [],
    }

    def fake_ingest(content: bytes, filename: str) -> dict:
        assert content == b"transaction_id,customer_id\n"
        assert filename == "transactions.csv"
        return expected

    monkeypatch.setattr(main, "ingest_csv", fake_ingest)

    response = client.post(
        "/imports/csv",
        files={
            "file": (
                "transactions.csv",
                b"transaction_id,customer_id\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 201
    assert response.json() == expected


def test_upload_rejects_non_csv_file(monkeypatch) -> None:
    def unexpected_ingest(*args, **kwargs):
        raise AssertionError("Ingestion should not run for a non-CSV file.")

    monkeypatch.setattr(main, "ingest_csv", unexpected_ingest)

    response = client.post(
        "/imports/csv",
        files={"file": ("transactions.txt", b"not a CSV", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only CSV files are supported."


def test_upload_rejects_file_over_configured_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(max_upload_size_mb=0),
    )

    response = client.post(
        "/imports/csv",
        files={"file": ("transactions.csv", b"x", "text/csv")},
    )

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


def test_upload_returns_400_for_invalid_csv_encoding(monkeypatch) -> None:
    def fake_ingest(content: bytes, filename: str) -> dict:
        raise CsvUploadError("The CSV must use UTF-8 encoding.")

    monkeypatch.setattr(main, "ingest_csv", fake_ingest)

    response = client.post(
        "/imports/csv",
        files={"file": ("transactions.csv", b"\xff\xfe", "text/csv")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "The CSV must use UTF-8 encoding."


def test_get_import_status_returns_404_for_unknown_batch() -> None:
    response = client.get(f"/imports/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Import batch not found."}
