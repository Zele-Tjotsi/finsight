from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from finsight.api import main

client = TestClient(main.app)


def test_analytics_overview_returns_metrics(monkeypatch) -> None:
    expected = {
        "total_transactions": 6,
        "unique_customers": 5,
        "total_amount_by_currency": {"LSL": "3000.50"},
        "transaction_counts_by_type": {
            "PAYMENT": 3,
            "REFUND": 2,
            "REVERSAL": 0,
            "TRANSFER": 1,
        },
        "date_range": {
            "earliest": "2026-10-15T09:30:00+00:00",
            "latest": "2026-10-16T12:00:00+00:00",
        },
    }

    monkeypatch.setattr(
        main,
        "get_analytics_overview",
        lambda: expected,
    )

    response = client.get("/analytics/overview")

    assert response.status_code == 200
    assert response.json() == expected


def test_analytics_overview_returns_500_on_database_error(
    monkeypatch,
) -> None:
    def raise_database_error():
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(
        main,
        "get_analytics_overview",
        raise_database_error,
    )

    response = client.get("/analytics/overview")

    assert response.status_code == 500
    assert response.json() == {"detail": "Analytics overview could not be retrieved."}


def test_transactions_over_time_daily_aggregation(monkeypatch) -> None:
    expected = {
        "interval": "day",
        "data": [
            {
                "date": "2026-10-15",
                "transaction_count": 3,
                "amount_by_currency": {"LSL": "1800.00"},
                "transaction_counts_by_type": {
                    "PAYMENT": 1,
                    "REFUND": 1,
                    "REVERSAL": 0,
                    "TRANSFER": 1,
                },
            },
            {
                "date": "2026-10-16",
                "transaction_count": 3,
                "amount_by_currency": {"LSL": "1200.50"},
                "transaction_counts_by_type": {
                    "PAYMENT": 2,
                    "REFUND": 1,
                    "REVERSAL": 0,
                    "TRANSFER": 0,
                },
            },
        ],
    }

    monkeypatch.setattr(
        main,
        "get_transactions_over_time",
        lambda interval: expected,
    )

    response = client.get("/analytics/transactions-over-time?interval=day")

    assert response.status_code == 200
    assert response.json() == expected


def test_transactions_over_time_monthly_aggregation(monkeypatch) -> None:
    expected = {
        "interval": "month",
        "data": [
            {
                "date": "2026-10-01",
                "transaction_count": 6,
                "amount_by_currency": {"LSL": "3000.50"},
                "transaction_counts_by_type": {
                    "PAYMENT": 3,
                    "REFUND": 2,
                    "REVERSAL": 0,
                    "TRANSFER": 1,
                },
            }
        ],
    }

    monkeypatch.setattr(
        main,
        "get_transactions_over_time",
        lambda interval: expected,
    )

    response = client.get("/analytics/transactions-over-time?interval=month")

    assert response.status_code == 200
    assert response.json() == expected


def test_transactions_over_time_rejects_invalid_interval() -> None:
    response = client.get("/analytics/transactions-over-time?interval=year")

    assert response.status_code == 400
    assert response.json() == {"detail": "Interval must be 'day' or 'month'."}


def test_transactions_over_time_returns_500_on_database_error(
    monkeypatch,
) -> None:
    def raise_database_error(interval: str = "day") -> dict:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(
        main,
        "get_transactions_over_time",
        raise_database_error,
    )

    response = client.get("/analytics/transactions-over-time?interval=day")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Time-series analytics could not be retrieved."
    }
