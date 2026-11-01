# syntax=docker/dockerfile:1
# One Dockerfile, several targets, so the images share their base layers and can't drift apart:
#
#   base          Python + uv + the light dependencies (FastAPI, SQLAlchemy, Celery, ...)
#   ├── api           + app code                     -> api, watcher, migrate   (small)
#   └── worker-deps   + Docling, CPU-only torch, models baked in
#       ├── worker        + app code                 -> Celery parse worker     (big)
#       └── worker-test   + pytest + tests            -> slow tests (compose profile "test")

FROM python:3.12-slim AS base

# uv binary from the official image, pinned to the version that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    BLOB_DIR=/data/blobs

# Dependencies first, so this layer is cached until uv.lock changes. The uv download cache is a
# BuildKit cache mount: it speeds up rebuilds but is never saved inside the image.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# The blob volume is created from this folder, so it starts out owned by the app user.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/blobs \
    && chown app /data/blobs


FROM base AS api
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS worker-deps
# libglib: needed by OpenCV (headless), which RapidOCR uses.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
# The `parse` group: Docling + torch from the CPU-only index (see pyproject.toml).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group parse

# Models are baked in BEFORE the app code is copied, so editing code never re-downloads them.
ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models
COPY app/parsing/download_models.py /tmp/download_models.py
RUN python /tmp/download_models.py "$DOCLING_ARTIFACTS_PATH" && rm /tmp/download_models.py
# From here on, a missing model is an error, never a silent download.
ENV HF_HUB_OFFLINE=1 \
    OMP_NUM_THREADS=4


FROM worker-deps AS worker
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
USER app
CMD ["celery", "-A", "app.worker", "worker", "-Q", "parse,celery", "--loglevel=info"]


FROM worker-deps AS worker-test
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group parse --group test
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY tests ./tests
USER app
# All slow tests live in tests/integration (the rest import ../scripts, which isn't in the image).
CMD ["pytest", "tests/integration", "-m", "slow", "-v", "-p", "no:cacheprovider"]
