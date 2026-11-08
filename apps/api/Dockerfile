FROM ghcr.io/astral-sh/uv:0.8.3 AS uv
FROM python:3.13.1-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api apps/api
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY apps/runner/pyproject.toml apps/runner/pyproject.toml
COPY apps/broker/pyproject.toml apps/broker/pyproject.toml
RUN uv sync --frozen --no-dev --package agentproof-api --no-editable

FROM python:3.13.1-slim-bookworm AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY apps/api/alembic.ini apps/api/alembic.ini
COPY apps/api/migrations apps/api/migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "agentproof_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
