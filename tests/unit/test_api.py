import json
import logging
from uuid import UUID

import pytest
from agentproof_api.app import create_app
from agentproof_api.logging import JsonFormatter
from agentproof_api.schemas import Dependencies
from agentproof_api.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr


class Probe:
    def __init__(self, postgres: bool = True, redis: bool = True) -> None:
        self.dependencies = Dependencies(
            postgres="up" if postgres else "down", redis="up" if redis else "down"
        )
        self.calls = 0

    async def check(self) -> Dependencies:
        self.calls += 1
        return self.dependencies


def application(probe: Probe | None = None) -> FastAPI:
    return create_app(Settings(_env_file=None, app_env="test"), probe=probe or Probe())


def test_liveness_does_not_probe_dependencies() -> None:
    probe = Probe(False, False)
    with TestClient(application(probe)) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert probe.calls == 0


@pytest.mark.parametrize(
    "postgres,redis", [(True, True), (False, True), (True, False), (False, False)]
)
def test_readiness_reflects_both_dependencies(postgres: bool, redis: bool) -> None:
    probe = Probe(postgres, redis)
    with TestClient(application(probe)) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == (200 if postgres and redis else 503)
    assert response.json()["dependencies"] == probe.dependencies.model_dump()
    assert probe.calls == 1
    if response.status_code == 503:
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["request_id"] == response.headers["x-request-id"]


def test_readiness_can_recover_without_restart() -> None:
    probe = Probe(False, False)
    with TestClient(application(probe)) as client:
        assert client.get("/api/v1/health/ready").status_code == 503
        probe.dependencies = Dependencies(postgres="up", redis="up")
        assert client.get("/api/v1/health/ready").status_code == 200


def test_server_generates_request_ids() -> None:
    with TestClient(application()) as client:
        first = client.get("/api/v1/version", headers={"X-Request-ID": "untrusted-value"})
        second = client.get("/api/v1/version")
    assert UUID(first.headers["x-request-id"]).version == 4
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {"version": "0.1.0", "api_version": "v1"}


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("GET", "/api/v1/not-present", 404),
        ("POST", "/api/v1/version", 405),
    ],
)
def test_http_errors_are_problems(method: str, path: str, status: int) -> None:
    with TestClient(application()) as client:
        response = client.request(
            method,
            path,
            headers={"Origin": "http://localhost:3000", "Content-Type": "application/json"},
        )
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["status"] == status
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_validation_never_reflects_input() -> None:
    app = application()

    @app.get("/validation")
    async def validation(value: int) -> dict[str, int]:
        return {"value": value}

    with TestClient(app) as client:
        response = client.get("/validation?value=private-input-sentinel")
    assert response.status_code == 422
    assert "private-input-sentinel" not in response.text


def test_unhandled_errors_never_reflect_exception() -> None:
    app = application()

    @app.get("/broken")
    async def broken() -> None:
        raise RuntimeError("private-exception-sentinel")

    with TestClient(app) as client:
        response = client.get("/broken")
    assert response.status_code == 500
    assert "private-exception-sentinel" not in response.text
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_logger_only_serializes_approved_fields() -> None:
    record = logging.LogRecord("agentproof", logging.INFO, "test", 1, "http_request", (), None)
    record.request_id = "request-sentinel"
    record.authorization = "private-header-sentinel"
    record.query_string = "private-query-sentinel"
    record.exc_text = "private-exception-sentinel"
    rendered = JsonFormatter().format(record)
    parsed = json.loads(rendered)
    assert parsed["request_id"] == "request-sentinel"
    assert parsed["timestamp"].endswith("+00:00")
    assert "private-" not in rendered


def test_request_logs_exclude_query_headers_and_unknown_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Install a fresh stream so captured output includes the real middleware logger.
    logger = logging.getLogger("agentproof")
    previous = logger.handlers[:]
    logger.handlers.clear()
    try:
        with TestClient(application()) as client:
            client.get(
                "/private-path-sentinel?token=private-query-sentinel",
                headers={"Authorization": "private-header-sentinel"},
            )
        output = capsys.readouterr().err
        assert '"event": "http_request"' in output
        assert "private-" not in output
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = previous


def test_settings_mask_secrets() -> None:
    settings = Settings(
        _env_file=None, vultr_inference_api_key=SecretStr("private-settings-sentinel")
    )
    assert "private-settings-sentinel" not in repr(settings)


def test_openapi_has_stable_operations_and_typed_readiness() -> None:
    schema = application().openapi()
    operation = schema["paths"]["/api/v1/health/ready"]["get"]
    assert operation["operationId"] == "getReadiness"
    assert operation["responses"]["503"]["content"]
    assert schema["components"]["schemas"]["Problem"]["additionalProperties"] is False


def test_production_refuses_public_development_credentials() -> None:
    with pytest.raises(ValueError, match="Production cannot use"):
        Settings(_env_file=None, app_env="production")


def test_settings_validation_errors_hide_environment_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "private-validation-sentinel")
    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None, app_env="production")
    assert "private-validation-sentinel" not in str(caught.value)
