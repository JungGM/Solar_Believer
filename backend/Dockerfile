FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home app \
    && mkdir /artifacts && chown app:app /artifacts
COPY backend /app/backend
COPY API /app/API
USER app
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
ENV RUFF_CACHE_DIR=/tmp/ruff_cache
USER root
COPY backend/requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
COPY pyproject.toml /app/pyproject.toml
COPY scripts /app/scripts
USER app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
