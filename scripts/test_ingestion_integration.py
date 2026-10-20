from uuid import uuid4

from sqlalchemy import delete, func, select

from finsight.database.connection import SessionLocal
from finsight.database.models import (
    DataQualityIssue,
    FinancialTransaction,
    ImportBatch,
)
from finsight.ingestion.service import ingest_csv


def make_csv(rows: list[str]) -> bytes:
    header = (
        "transaction_id,customer_id,transaction_date,"
        "transaction_type,amount,currency,description\n"
    )
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def main() -> None:
    customer_id = f"INTEGRATION-{uuid4().hex[:12]}"
    transaction_id = f"TXN-{uuid4().hex[:12]}"
    created_batch_ids: list[str] = []

    valid_row = (
        f"{transaction_id},{customer_id},2026-10-15T09:30:00+02:00,"
        "PAYMENT,250.00,LSL,Integration test"
    )

    try:
        print("\nTEST 1: Valid CSV import")
        result = ingest_csv(make_csv([valid_row]), "integration-valid.csv")
        created_batch_ids.append(result["batch_id"])

        assert result["status"] == "COMPLETED", result
        assert result["total_rows"] == 1, result
        assert result["accepted_rows"] == 1, result
        assert result["rejected_rows"] == 0, result
        assert result["duplicate_rows"] == 0, result

        print("PASS:", result)

        print("\nTEST 2: Duplicate across separate uploads")
        duplicate_result = ingest_csv(
            make_csv([valid_row]),
            "integration-duplicate.csv",
        )
        created_batch_ids.append(duplicate_result["batch_id"])

        assert duplicate_result["accepted_rows"] == 0, duplicate_result
        assert duplicate_result["duplicate_rows"] == 1, duplicate_result
        assert duplicate_result["status"] == "COMPLETED_WITH_ERRORS", (
            duplicate_result
        )

        print("PASS:", duplicate_result)

        print("\nTEST 3: Valid and invalid rows in one upload")
        second_valid_row = (
            f"TXN-{uuid4().hex[:12]},{customer_id},"
            "2026-10-15T10:30:00+02:00,"
            "TRANSFER,75.50,LSL,Second valid transaction"
        )
        invalid_row = (
            f"TXN-{uuid4().hex[:12]},{customer_id},"
            "not-a-date,PAYMENT,not-a-number,LSL,Invalid row"
        )

        mixed_result = ingest_csv(
            make_csv([second_valid_row, invalid_row]),
            "integration-mixed.csv",
        )
        created_batch_ids.append(mixed_result["batch_id"])

        assert mixed_result["total_rows"] == 2, mixed_result
        assert mixed_result["accepted_rows"] == 1, mixed_result
        assert mixed_result["rejected_rows"] == 1, mixed_result
        assert mixed_result["status"] == "COMPLETED_WITH_ERRORS", (
            mixed_result
        )

        print("PASS:", mixed_result)

        print("\nTEST 4: Verify persisted database records")
        with SessionLocal() as session:
            transaction_count = session.scalar(
                select(func.count())
                .select_from(FinancialTransaction)
                .where(FinancialTransaction.customer_id == customer_id)
            )

            batch_count = session.scalar(
                select(func.count())
                .select_from(ImportBatch)
                .where(ImportBatch.id.in_(created_batch_ids))
            )

            issue_count = session.scalar(
                select(func.count())
                .select_from(DataQualityIssue)
                .where(
                    DataQualityIssue.import_batch_id.in_(created_batch_ids)
                )
            )

            assert transaction_count == 2, transaction_count
            assert batch_count == 3, batch_count
            assert issue_count >= 2, issue_count

        print(
            "PASS: 2 transactions, 3 import batches, "
            f"and {issue_count} recorded quality issues."
        )

        print("\nALL INTEGRATION TESTS PASSED.")

    finally:
        print("\nCleaning up integration-test records...")

        with SessionLocal.begin() as session:
            batch_ids = [uuid for uuid in created_batch_ids]

            if batch_ids:
                session.execute(
                    delete(DataQualityIssue).where(
                        DataQualityIssue.import_batch_id.in_(batch_ids)
                    )
                )
                session.execute(
                    delete(FinancialTransaction).where(
                        FinancialTransaction.import_batch_id.in_(batch_ids)
                    )
                )
                session.execute(
                    delete(ImportBatch).where(ImportBatch.id.in_(batch_ids))
                )

        print("Cleanup completed.")


if __name__ == "__main__":
    main()
