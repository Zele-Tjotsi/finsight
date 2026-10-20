from decimal import Decimal
from io import StringIO

import pytest

from finsight.ingestion.csv_parser import parse_csv, parse_csv_bytes

HEADER = (
    "transaction_id,customer_id,transaction_date,transaction_type,"
    "amount,currency,description\n"
)


def test_parses_valid_transaction() -> None:
    content = (
        HEADER + "TXN-1001,CUST-001,2026-10-15T09:30:00+02:00,"
        "PAYMENT,250.00,LSL,Groceries\n"
    )

    result = parse_csv(StringIO(content))

    assert result.total_rows == 1
    assert result.accepted_rows == 1
    assert result.rejected_rows == 0
    assert result.duplicate_rows == 0
    assert result.transactions[0].amount == Decimal("250.00")
    assert result.transactions[0].currency == "LSL"


def test_accepts_negative_refund_amount() -> None:
    content = (
        HEADER + "TXN-1002,CUST-001,2026-10-15T11:15:00+02:00,"
        "REFUND,-50.00,LSL,Refund\n"
    )

    result = parse_csv(StringIO(content))

    assert result.accepted_rows == 1
    assert result.transactions[0].amount == Decimal("-50.00")


def test_rejects_timezone_naive_timestamp() -> None:
    content = (
        HEADER + "TXN-1003,CUST-001,2026-10-15T09:30:00,"
        "PAYMENT,250.00,LSL,Missing timezone\n"
    )

    result = parse_csv(StringIO(content))

    assert result.accepted_rows == 0
    assert result.rejected_rows == 1
    assert result.issues[0].issue_code == "INVALID_TRANSACTION_DATE"


def test_rejects_malformed_amount() -> None:
    content = (
        HEADER + "TXN-1004,CUST-001,2026-10-15T09:30:00+02:00,"
        "PAYMENT,not-a-number,LSL,Bad amount\n"
    )

    result = parse_csv(StringIO(content))

    assert result.accepted_rows == 0
    assert result.rejected_rows == 1
    assert any(issue.issue_code == "INVALID_AMOUNT" for issue in result.issues)


def test_rejects_unsupported_transaction_type() -> None:
    content = (
        HEADER + "TXN-1005,CUST-001,2026-10-15T09:30:00+02:00,"
        "WITHDRAWAL,100.00,LSL,Unsupported type\n"
    )

    result = parse_csv(StringIO(content))

    assert result.accepted_rows == 0
    assert result.rejected_rows == 1
    assert any(
        issue.issue_code == "INVALID_TRANSACTION_TYPE" for issue in result.issues
    )


def test_detects_duplicate_transaction_within_file() -> None:
    row = "TXN-1006,CUST-001,2026-10-15T09:30:00+02:00,PAYMENT,100.00,LSL,Payment\n"

    result = parse_csv(StringIO(HEADER + row + row))

    assert result.total_rows == 2
    assert result.accepted_rows == 1
    assert result.duplicate_rows == 1
    assert result.rejected_rows == 0
    assert result.issues[0].issue_code == "DUPLICATE_IN_FILE"


def test_allows_same_transaction_id_for_different_customers() -> None:
    row_one = "TXN-1007,CUST-001,2026-10-15T09:30:00+02:00,PAYMENT,100.00,LSL,Payment\n"
    row_two = "TXN-1007,CUST-002,2026-10-15T09:30:00+02:00,PAYMENT,100.00,LSL,Payment\n"

    result = parse_csv(StringIO(HEADER + row_one + row_two))

    assert result.accepted_rows == 2
    assert result.duplicate_rows == 0


def test_reports_missing_required_columns() -> None:
    content = "transaction_id,customer_id,amount\nTXN-1008,CUST-001,100.00\n"

    result = parse_csv(StringIO(content))

    assert result.accepted_rows == 0
    assert result.total_rows == 0
    assert result.issues[0].issue_code == "MISSING_COLUMNS"


def test_supports_utf8_bom() -> None:
    content = (
        HEADER + "TXN-1009,CUST-001,2026-10-15T09:30:00+02:00,PAYMENT,100.00,LSL,Cafe\n"
    ).encode("utf-8-sig")

    result = parse_csv_bytes(content)

    assert result.accepted_rows == 1


def test_rejects_amount_with_more_than_two_decimal_places() -> None:
    content = (
        HEADER + "TXN-1010,CUST-001,2026-10-15T09:30:00+02:00,"
        "PAYMENT,10.123,LSL,Too precise\n"
    )

    result = parse_csv(StringIO(content))

    assert result.accepted_rows == 0
    assert any(issue.issue_code == "INVALID_AMOUNT" for issue in result.issues)


def test_invalid_utf8_is_reported_to_caller() -> None:
    with pytest.raises(UnicodeDecodeError):
        parse_csv_bytes(b"\xff\xfe\xfa")
