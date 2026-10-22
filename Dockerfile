FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the application and its declared dependencies.
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Alembic migrations are required for explicit schema upgrades.
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Run the application as a non-root user.
RUN useradd --system --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "finsight.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
