import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TextIO

from finsight.database.models.transaction import TransactionType

REQUIRED_COLUMNS = {
    "transaction_id",
    "customer_id",
    "transaction_date",
    "transaction_type",
    "amount",
    "currency",
}

OPTIONAL_COLUMNS = {"description"}

SUPPORTED_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS

CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class ParsedTransaction:
    transaction_id: str
    customer_id: str
    transaction_date: datetime
    transaction_type: TransactionType
    amount: Decimal
    currency: str
    row_number: int
    description: str | None = None


@dataclass(frozen=True)
class ValidationIssue:
    row_number: int
    issue_code: str
    message: str


@dataclass
class CsvParseResult:
    transactions: list[ParsedTransaction] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    total_rows: int = 0
    duplicate_rows: int = 0

    @property
    def rejected_rows(self) -> int:
        return len(
            {
                issue.row_number
                for issue in self.issues
                if issue.issue_code != "DUPLICATE_IN_FILE"
            }
        )

    @property
    def accepted_rows(self) -> int:
        return len(self.transactions)


def _parse_transaction_date(value: str) -> datetime:
    normalized = value.strip()

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Transaction date must include a timezone.")

    return parsed


def _parse_amount(value: str) -> Decimal:
    amount = Decimal(value.strip())

    if not amount.is_finite():
        raise ValueError("Amount must be a finite decimal number.")

    if amount.as_tuple().exponent < -2:
        raise ValueError("Amount must have at most two decimal places.")

    if amount.copy_abs() >= Decimal("10000000000000000"):
        raise ValueError("Amount exceeds the supported numeric range.")

    return amount


def _validate_row(
    row: dict[str, str | None],
    row_number: int,
) -> tuple[ParsedTransaction | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []

    def value(column: str) -> str:
        return (row.get(column) or "").strip()

    transaction_id = value("transaction_id")
    customer_id = value("customer_id")
    transaction_date_text = value("transaction_date")
    transaction_type_text = value("transaction_type").upper()
    amount_text = value("amount")
    currency = value("currency").upper()
    description = value("description")

    for column, column_value in (
        ("transaction_id", transaction_id),
        ("customer_id", customer_id),
        ("transaction_date", transaction_date_text),
        ("transaction_type", transaction_type_text),
        ("amount", amount_text),
        ("currency", currency),
    ):
        if not column_value:
            issues.append(
                ValidationIssue(
                    row_number,
                    "MISSING_REQUIRED_VALUE",
                    f"Required field '{column}' is empty.",
                )
            )

    parsed_date = None
    if transaction_date_text:
        try:
            parsed_date = _parse_transaction_date(transaction_date_text)
        except ValueError:
            issues.append(
                ValidationIssue(
                    row_number,
                    "INVALID_TRANSACTION_DATE",
                    "Transaction date must be a valid ISO 8601 timestamp "
                    "with a timezone.",
                )
            )

    parsed_type = None
    if transaction_type_text:
        try:
            parsed_type = TransactionType(transaction_type_text)
        except ValueError:
            issues.append(
                ValidationIssue(
                    row_number,
                    "INVALID_TRANSACTION_TYPE",
                    f"Unsupported transaction type: '{transaction_type_text}'.",
                )
            )

    parsed_amount = None
    if amount_text:
        try:
            parsed_amount = _parse_amount(amount_text)
        except (InvalidOperation, ValueError):
            issues.append(
                ValidationIssue(
                    row_number,
                    "INVALID_AMOUNT",
                    "Amount must be a finite decimal with at most "
                    "two decimal places and within the supported range.",
                )
            )

    if currency and not CURRENCY_PATTERN.fullmatch(currency):
        issues.append(
            ValidationIssue(
                row_number,
                "INVALID_CURRENCY",
                "Currency must be a three-letter uppercase currency code.",
            )
        )

    if len(transaction_id) > 128:
        issues.append(
            ValidationIssue(
                row_number,
                "FIELD_TOO_LONG",
                "transaction_id cannot exceed 128 characters.",
            )
        )

    if len(customer_id) > 128:
        issues.append(
            ValidationIssue(
                row_number,
                "FIELD_TOO_LONG",
                "customer_id cannot exceed 128 characters.",
            )
        )

    if len(currency) > 3:
        issues.append(
            ValidationIssue(
                row_number,
                "FIELD_TOO_LONG",
                "currency cannot exceed 3 characters.",
            )
        )

    if len(description) > 500:
        issues.append(
            ValidationIssue(
                row_number,
                "FIELD_TOO_LONG",
                "description cannot exceed 500 characters.",
            )
        )

    if issues:
        return None, issues

    assert parsed_date is not None
    assert parsed_type is not None
    assert parsed_amount is not None

    return (
        ParsedTransaction(
            transaction_id=transaction_id,
            customer_id=customer_id,
            transaction_date=parsed_date,
            transaction_type=parsed_type,
            amount=parsed_amount,
            currency=currency,
            row_number=row_number,
            description=description or None,
        ),
        [],
    )


def parse_csv(file: TextIO) -> CsvParseResult:
    result = CsvParseResult()

    reader = csv.DictReader(file)

    if reader.fieldnames is None:
        result.issues.append(
            ValidationIssue(
                1,
                "MISSING_HEADER",
                "CSV file is empty or does not contain a header row.",
            )
        )
        return result

    normalized_headers = [
        header.strip().lower() if header is not None else ""
        for header in reader.fieldnames
    ]

    if len(normalized_headers) != len(set(normalized_headers)):
        result.issues.append(
            ValidationIssue(
                1,
                "DUPLICATE_COLUMNS",
                "CSV contains duplicate column names.",
            )
        )
        return result

    missing_columns = REQUIRED_COLUMNS - set(normalized_headers)

    if missing_columns:
        result.issues.append(
            ValidationIssue(
                1,
                "MISSING_COLUMNS",
                "Missing required columns: " + ", ".join(sorted(missing_columns)),
            )
        )
        return result

    seen_transactions: set[tuple[str, str]] = set()

    for row_number, original_row in enumerate(reader, start=2):
        row = {
            (key.strip().lower() if key is not None else ""): value
            for key, value in original_row.items()
        }

        if not any((value or "").strip() for value in row.values()):
            continue

        result.total_rows += 1

        transaction, issues = _validate_row(row, row_number)

        if issues:
            result.issues.extend(issues)
            continue

        assert transaction is not None

        key = (transaction.customer_id, transaction.transaction_id)

        if key in seen_transactions:
            result.duplicate_rows += 1
            result.issues.append(
                ValidationIssue(
                    row_number,
                    "DUPLICATE_IN_FILE",
                    "Transaction identifier has already appeared for "
                    "this customer in the current file.",
                )
            )
            continue

        seen_transactions.add(key)
        result.transactions.append(transaction)

    return result


def parse_csv_bytes(content: bytes) -> CsvParseResult:
    text = io.TextIOWrapper(
        io.BytesIO(content),
        encoding="utf-8-sig",
        errors="strict",
        newline="",
    )

    return parse_csv(text)
