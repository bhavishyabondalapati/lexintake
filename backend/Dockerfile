FROM python:3.12-slim

# uv binary from the official image, pinned to the version that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Dependencies first, so this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

COPY app ./app

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
