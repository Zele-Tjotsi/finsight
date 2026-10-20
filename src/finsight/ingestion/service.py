from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from finsight.config import get_settings
from finsight.database.connection import SessionLocal
from finsight.database.models import (
    DataQualityIssue,
    FinancialTransaction,
    ImportBatch,
    ImportStatus,
)
from finsight.database.models.base import utc_now
from finsight.ingestion.csv_parser import parse_csv_bytes


class CsvUploadError(ValueError):
    """Raised when an uploaded CSV cannot be processed."""


class UploadTooLargeError(CsvUploadError):
    """Raised when an upload exceeds the configured size limit."""


def _safe_filename(filename: str) -> str:
    """Keep only the filename, not any supplied directory path."""
    normalized = filename.replace("\\", "/")
    name = PurePosixPath(normalized).name.strip()

    if not name or name in {".", ".."}:
        name = "upload.csv"

    return name[:255]


def ingest_csv(content: bytes, filename: str) -> dict:
    """Validate a CSV and persist its import results atomically."""
    settings = get_settings()
    maximum_bytes = settings.max_upload_size_mb * 1024 * 1024

    if len(content) > maximum_bytes:
        raise UploadTooLargeError(
            f"Upload exceeds the {settings.max_upload_size_mb} MB limit."
        )

    if not filename.lower().endswith(".csv"):
        raise CsvUploadError("Only CSV files are supported.")

    try:
        parsed = parse_csv_bytes(content)
    except UnicodeDecodeError as exc:
        raise CsvUploadError("The CSV must use UTF-8 encoding.") from exc

    batch_id = uuid4()
    stored_filename = _safe_filename(filename)

    session = SessionLocal()

    try:
        with session.begin():
            batch = ImportBatch(
                id=batch_id,
                filename=stored_filename,
                status=ImportStatus.PROCESSING,
            )
            session.add(batch)
            session.flush()

            # Persist validation issues produced by the CSV parser.
            for issue in parsed.issues:
                session.add(
                    DataQualityIssue(
                        import_batch_id=batch_id,
                        row_number=issue.row_number,
                        issue_code=issue.issue_code,
                        message=issue.message,
                    )
                )

            inserted_keys: set[tuple[str, str]] = set()

            if parsed.transactions:
                values = [
                    {
                        "import_batch_id": batch_id,
                        "transaction_id": transaction.transaction_id,
                        "customer_id": transaction.customer_id,
                        "transaction_date": transaction.transaction_date,
                        "transaction_type": transaction.transaction_type,
                        "amount": transaction.amount,
                        "currency": transaction.currency,
                        "description": transaction.description,
                    }
                    for transaction in parsed.transactions
                ]

                statement = (
                    insert(FinancialTransaction)
                    .values(values)
                    .on_conflict_do_nothing(
                        constraint="uq_transaction_customer_external_id"
                    )
                    .returning(
                        FinancialTransaction.customer_id,
                        FinancialTransaction.transaction_id,
                    )
                )

                inserted_rows = session.execute(statement).all()

                inserted_keys = {
                    (row.customer_id, row.transaction_id) for row in inserted_rows
                }

            # A transaction not returned by INSERT ... RETURNING conflicted
            # with an existing row under the database uniqueness constraint.
            database_duplicates = [
                transaction
                for transaction in parsed.transactions
                if (
                    transaction.customer_id,
                    transaction.transaction_id,
                )
                not in inserted_keys
            ]

            for transaction in database_duplicates:
                session.add(
                    DataQualityIssue(
                        import_batch_id=batch_id,
                        row_number=transaction.row_number,
                        issue_code="DUPLICATE_IN_DATABASE",
                        message=(
                            "Transaction identifier already exists for "
                            "this customer in the database."
                        ),
                    )
                )

            rejected_rows = parsed.rejected_rows

            # Header errors are recorded as issues, but are not rejected
            # transaction rows because no transaction data rows were read.
            if parsed.total_rows == 0:
                rejected_rows = 0

            duplicate_count = parsed.duplicate_rows + len(database_duplicates)

            has_errors = bool(parsed.issues or database_duplicates)

            batch.total_rows = parsed.total_rows
            batch.accepted_rows = len(inserted_keys)
            batch.rejected_rows = rejected_rows
            batch.duplicate_rows = duplicate_count
            batch.status = (
                ImportStatus.COMPLETED_WITH_ERRORS
                if has_errors
                else ImportStatus.COMPLETED
            )
            batch.completed_at = utc_now()

            response = {
                "batch_id": str(batch_id),
                "filename": stored_filename,
                "status": batch.status.value,
                "total_rows": batch.total_rows,
                "accepted_rows": batch.accepted_rows,
                "rejected_rows": batch.rejected_rows,
                "duplicate_rows": batch.duplicate_rows,
                "issues": [
                    {
                        "row_number": issue.row_number,
                        "issue_code": issue.issue_code,
                        "message": issue.message,
                    }
                    for issue in parsed.issues
                ]
                + [
                    {
                        "row_number": transaction.row_number,
                        "issue_code": "DUPLICATE_IN_DATABASE",
                        "message": (
                            "Transaction identifier already exists for "
                            "this customer in the database."
                        ),
                    }
                    for transaction in database_duplicates
                ],
            }

        return response

    except SQLAlchemyError:
        # The transaction context rolls back all changes on failure.
        session.rollback()

        # Record a failed batch separately, when the database is available.
        try:
            with session.begin():
                session.add(
                    ImportBatch(
                        id=batch_id,
                        filename=stored_filename,
                        status=ImportStatus.FAILED,
                        total_rows=0,
                        accepted_rows=0,
                        rejected_rows=0,
                        duplicate_rows=0,
                        completed_at=datetime.now(UTC),
                    )
                )
        except SQLAlchemyError:
            # If the database itself is unavailable, even failure tracking
            # may be impossible. Preserve the original exception.
            session.rollback()

        raise

    finally:
        session.close()
