FROM python:3.12-slim AS base

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock* ./
COPY packages/ packages/
COPY services/ services/
COPY apps/ apps/

RUN uv sync --frozen --no-dev || uv sync --no-dev

FROM base AS api
WORKDIR /app/apps/api
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "note_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
WORKDIR /app/apps/worker
CMD ["uv", "run", "python", "-m", "note_worker.main"]
