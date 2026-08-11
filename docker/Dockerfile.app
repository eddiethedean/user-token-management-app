# Access Registry app image for Workbench integration tests.
FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker/entrypoint-app.sh /entrypoint-app.sh

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e . \
    && chmod +x /entrypoint-app.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint-app.sh"]
