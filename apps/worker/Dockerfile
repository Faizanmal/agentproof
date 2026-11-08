FROM ghcr.io/astral-sh/uv:0.8.3 AS uv
FROM python:3.13.1-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/worker apps/worker
COPY apps/api apps/api
COPY apps/runner/pyproject.toml apps/runner/pyproject.toml
COPY apps/broker/pyproject.toml apps/broker/pyproject.toml
RUN uv sync --frozen --no-dev --package agentproof-worker --no-editable \
    && test ! -e /app/.venv/lib/python3.13/site-packages/_editable_impl_agentproof_worker.pth \
    && /app/.venv/bin/python -c "import agentproof_worker, pathlib; p=pathlib.Path(agentproof_worker.__file__); assert 'site-packages' in p.as_posix(), p"

FROM python:3.13.1-slim-bookworm AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD arq --check agentproof_worker.main.WorkerSettings
CMD ["arq", "agentproof_worker.main.WorkerSettings"]
