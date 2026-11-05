from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.exceptions import HTTPException
from starlette.responses import Response

from agentproof_api import __version__
from agentproof_api.dependencies import DependencyProbe, ServiceProbe
from agentproof_api.logging import configure_logging
from agentproof_api.schemas import Health, Problem, Ready, Version
from agentproof_api.settings import Settings


def problem_response(problem: Problem) -> JSONResponse:
    return JSONResponse(
        problem.model_dump(exclude_none=True),
        status_code=problem.status,
        media_type="application/problem+json",
        headers={"X-Request-ID": problem.request_id, "Cache-Control": "no-store"},
    )


def create_app(settings: Settings | None = None, probe: DependencyProbe | None = None) -> FastAPI:
    config = settings or Settings()
    logger = configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if probe is not None:
            app.state.probe = probe
            yield
            return
        engine = create_async_engine(config.database_url.get_secret_value(), pool_pre_ping=True)
        redis: Redis = Redis.from_url(config.redis_url.get_secret_value())
        app.state.probe = ServiceProbe(engine, redis, config.dependency_timeout_seconds)
        try:
            yield
        finally:
            try:
                await redis.aclose()
            finally:
                await engine.dispose()

    app = FastAPI(
        title="AgentProof API",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json" if config.app_env != "production" else None,
        docs_url="/api/v1/docs" if config.app_env != "production" else None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Generate IDs server-side. Untrusted header values are never reflected or logged.
        request.state.request_id = str(uuid4())
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            response = problem_response(
                Problem(
                    title="Internal Server Error",
                    status=500,
                    detail="The request could not be completed.",
                    request_id=request.state.request_id,
                )
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        route = request.scope.get("route")
        logger.info(
            "http_request",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - start) * 1000, 2),
            },
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        titles = {404: "Not Found", 405: "Method Not Allowed"}
        title = titles.get(exc.status_code, "Request Failed")
        return problem_response(
            Problem(
                title=title,
                status=exc.status_code,
                detail=title,
                request_id=request.state.request_id,
            )
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not return validation inputs: they can contain credentials.
        return problem_response(
            Problem(
                title="Unprocessable Content",
                status=422,
                detail="Request does not match the API contract.",
                request_id=request.state.request_id,
            )
        )

    @app.get("/api/v1/health/live", response_model=Health, operation_id="getLiveness")
    async def live() -> Health:
        return Health(status="alive")

    @app.get(
        "/api/v1/health/ready",
        response_model=Ready,
        operation_id="getReadiness",
        responses={
            503: {
                "model": Problem,
                "description": "A dependency is unavailable",
                "content": {
                    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                },
            }
        },
    )
    async def ready(request: Request) -> Ready | JSONResponse:
        dependency_probe: DependencyProbe = request.app.state.probe
        dependencies = await dependency_probe.check()
        if dependencies.postgres != "up" or dependencies.redis != "up":
            return problem_response(
                Problem(
                    title="Service Unavailable",
                    status=503,
                    detail="Required dependencies are unavailable.",
                    request_id=request.state.request_id,
                    dependencies=dependencies,
                )
            )
        return Ready(status="ready", dependencies=dependencies)

    @app.get("/api/v1/version", response_model=Version, operation_id="getVersion")
    async def version() -> Version:
        return Version(version=__version__)

    return app
