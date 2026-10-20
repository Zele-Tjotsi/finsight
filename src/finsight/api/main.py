import logging
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from finsight.analytics.overview import (
    get_analytics_overview,
    get_transactions_over_time,
)
from finsight.config import get_settings
from finsight.database.connection import (
    SessionLocal,
    check_database_connection,
)
from finsight.database.models import DataQualityIssue, ImportBatch
from finsight.ingestion.service import (
    CsvUploadError,
    UploadTooLargeError,
    ingest_csv,
)

logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="Financial data ingestion, analytics, and risk intelligence.",
    version="0.1.0",
)


@app.get("/health", tags=["Operations"])
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "finsight-api",
    }


@app.get("/health/ready", tags=["Operations"])
def readiness_check() -> dict[str, str]:
    if not check_database_connection():
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable.",
        )

    return {
        "status": "ready",
        "database": "connected",
    }


@app.post("/imports/csv", tags=["Imports"], status_code=201)
# FastAPI requires File(...) here to declare a required file upload.
async def upload_csv(file: UploadFile = File(...)) -> dict:  # noqa: B008
    """Upload and process a CSV containing financial transactions."""
    filename = file.filename

    if not filename or not filename.strip():
        raise HTTPException(
            status_code=400,
            detail="A filename is required.",
        )

    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Only CSV files are supported.",
        )

    maximum_bytes = settings.max_upload_size_mb * 1024 * 1024

    try:
        # Read at most one byte beyond the limit to detect oversized uploads
        # without loading an arbitrarily large file into application memory.
        content = await file.read(maximum_bytes + 1)
    finally:
        await file.close()

    if len(content) > maximum_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Upload exceeds the configured {settings.max_upload_size_mb} MB limit."
            ),
        )

    try:
        return ingest_csv(content, filename)

    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    except CsvUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except SQLAlchemyError as exc:
        logger.exception("Database error while processing CSV upload.")
        raise HTTPException(
            status_code=500,
            detail="The import could not be completed due to a database error.",
        ) from exc


@app.get("/imports/{batch_id}", tags=["Imports"])
def get_import_status(batch_id: UUID) -> dict:
    """Retrieve persisted import statistics and data-quality issues."""
    try:
        with SessionLocal() as session:
            batch = session.get(ImportBatch, batch_id)

            if batch is None:
                raise HTTPException(
                    status_code=404,
                    detail="Import batch not found.",
                )

            issues = session.scalars(
                select(DataQualityIssue)
                .where(DataQualityIssue.import_batch_id == batch_id)
                .order_by(
                    DataQualityIssue.row_number,
                    DataQualityIssue.created_at,
                    DataQualityIssue.id,
                )
            ).all()

            return {
                "batch_id": str(batch.id),
                "filename": batch.filename,
                "status": batch.status.value,
                "total_rows": batch.total_rows,
                "accepted_rows": batch.accepted_rows,
                "rejected_rows": batch.rejected_rows,
                "duplicate_rows": batch.duplicate_rows,
                "started_at": batch.started_at.isoformat(),
                "completed_at": (
                    batch.completed_at.isoformat() if batch.completed_at else None
                ),
                "issues": [
                    {
                        "row_number": issue.row_number,
                        "issue_code": issue.issue_code,
                        "message": issue.message,
                    }
                    for issue in issues
                ],
            }

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception("Database error while retrieving import status.")
        raise HTTPException(
            status_code=500,
            detail="Import status could not be retrieved.",
        ) from exc


@app.get("/analytics/overview", tags=["Analytics"])
def analytics_overview() -> dict:
    """Return aggregate financial transaction metrics."""
    try:
        return get_analytics_overview()
    except SQLAlchemyError as exc:
        logger.exception("Database error while calculating analytics overview.")
        raise HTTPException(
            status_code=500,
            detail="Analytics overview could not be retrieved.",
        ) from exc


@app.get("/analytics/transactions-over-time", tags=["Analytics"])
def transactions_over_time(interval: str = "day") -> dict:
    """Return transaction metrics grouped by day or month."""
    try:
        return get_transactions_over_time(interval)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Database error while calculating time-series analytics.")
        raise HTTPException(
            status_code=500,
            detail="Time-series analytics could not be retrieved.",
        ) from exc
