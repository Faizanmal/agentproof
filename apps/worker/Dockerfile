FROM ghcr.io/astral-sh/uv:0.8.3 AS uv
FROM python:3.13.1-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/worker apps/worker
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/runner/pyproject.toml apps/runner/pyproject.toml
RUN uv sync --frozen --no-dev --package agentproof-worker --no-editable

FROM python:3.13.1-slim-bookworm AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
# Scaffold validation only. No consumer or sandbox access is enabled yet.
CMD ["agentproof-worker", "--check"]
