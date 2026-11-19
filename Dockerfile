# Small, non-root image for Cloud Run Jobs (or any container scheduler).
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[bigquery]"

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=build /install /usr/local
RUN useradd --create-home --uid 10001 app
USER app
WORKDIR /home/app
# Configuration comes from FX_* environment variables (see README).
ENTRYPOINT ["fx-ingest"]
