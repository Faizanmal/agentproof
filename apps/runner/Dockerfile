# Build this image in the isolated sandbox image pipeline, inspect it, then configure the
# broker with the resulting immutable registry@sha256 digest. Never deploy by mutable tag.
# Production: docker build --target runtime (default last stage). Prefer DOCKER_BUILDKIT=1.
FROM ghcr.io/astral-sh/uv:0.8.3 AS uv
FROM python:3.13.1-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY apps/broker/pyproject.toml apps/broker/pyproject.toml
COPY apps/runner apps/runner
# Workspace packages default to editable; --no-editable must land real modules in
# site-packages because the runtime stage only copies /.venv (not apps/runner/src).
# Do not use `uv run` after sync: it re-syncs workspace members as editable and
# strips the installed package from site-packages.
RUN uv sync --frozen --no-dev --package agentproof-runner --no-editable \
    && /app/.venv/bin/playwright install --with-deps chromium \
    && test ! -e /app/.venv/lib/python3.13/site-packages/_editable_impl_agentproof_runner.pth \
    && /app/.venv/bin/python -c "import agentproof_runner, pathlib; p=pathlib.Path(agentproof_runner.__file__); assert p.is_file() and 'site-packages' in p.as_posix(), p"

FROM python:3.13.1-slim-bookworm AS runtime
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=build /app/.venv /app/.venv
COPY --from=build /ms-playwright /ms-playwright
RUN /app/.venv/bin/python -m playwright install-deps chromium \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && chown -R 10001:10001 /app
ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import agentproof_runner"
ENTRYPOINT ["agentproof-runner"]

# Opt-in CI/manual real-browser gate. Placed AFTER runtime so legacy builders with
# --target runtime (or default last-needed chain) do not execute this stage.
# Dev group lives on the workspace root pyproject, not apps/runner.
FROM build AS browser-test
RUN uv sync --frozen --group dev
COPY tests/e2e /tests/e2e
ENV PYTHONPATH=/app/apps/runner/src AGENTPROOF_RUNNER_BROWSER=1
CMD ["uv", "run", "--frozen", "--package", "agentproof-runner", "pytest", "/tests/e2e", "-q"]
