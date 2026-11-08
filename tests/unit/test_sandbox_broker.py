import hashlib
import hmac
import subprocess
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import pytest
from agentproof_broker.main import (
    Broker,
    JobEnvelope,
    Limits,
    Plan,
    SandboxJob,
    Step,
    _signature,
    authenticated,
)
from agentproof_runner.main import _host_allowed, _path_allowed
from fastapi import FastAPI, HTTPException
from starlette.requests import Request


def job(**overrides: object) -> SandboxJob:
    plan = Plan.model_validate(
        {
            "schema_version": "1.0",
            "goal": "Capture the owned fixture",
            "risk_level": "LOW",
            "steps": [
                {"id": "s1", "action": "navigate", "url_path": "/", "requires_approval": False},
                {
                    "id": "s2",
                    "action": "assert_visible",
                    "target": {"strategy": "role", "role": "heading", "name": "Demo"},
                    "assertion": {"kind": "visible", "expected": True},
                    "requires_approval": False,
                },
            ],
            "assumptions": [],
            "success_criteria": ["Owned fixture heading is visible"],
            "warnings": [],
        }
    )
    plan_digest = sha256(
        __import__("json")
        .dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        .encode()
    ).hexdigest()
    values = {
        "job_id": uuid4(),
        "run_id": uuid4(),
        "project_id": uuid4(),
        "policy_hash": "a" * 64,
        "approved_origins": ["https://example.com"],
        "allowed_paths": ["/", "/*"],
        "plan": plan,
        "plan_digest": plan_digest,
        "limits": Limits(
            max_duration_seconds=60,
            max_actions=10,
            memory_bytes=512 * 1024 * 1024,
            cpus=1,
            pids=64,
            artifact_bytes=1024 * 1024,
        ),
        "authorization_expires_at": datetime.now(UTC) + timedelta(days=30),
        "expires_at": datetime.now(UTC) + timedelta(seconds=90),
        "nonce": uuid4().hex + uuid4().hex,
    }
    values.update(overrides)
    return SandboxJob.model_validate(values)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://2130706433",
        "https://example.com:invalid",
        "https://localhost",
        "https://metadata.google.internal",
        "https://user@example.com",
        "https://example.com/path",
        "https://EXAMPLE.com",
    ],
)
def test_job_rejects_unsafe_or_noncanonical_origins(origin: str) -> None:
    with pytest.raises(ValueError):
        job(approved_origins=[origin])


def test_job_rejects_external_url_and_unbounded_path() -> None:
    with pytest.raises(ValueError):
        Step.model_validate(
            {
                "id": "s1",
                "action": "navigate",
                "url_path": "https://evil.test",
                "requires_approval": False,
            }
        )
    with pytest.raises(ValueError):
        job(allowed_paths=["//evil.test"])


def test_job_rejects_plan_above_broker_action_limit() -> None:
    plan = Plan.model_validate(
        {
            "schema_version": "1.0",
            "goal": "bounded",
            "risk_level": "LOW",
            "steps": [
                {"id": "s1", "action": "navigate", "url_path": "/", "requires_approval": False},
                {
                    "id": "s2",
                    "action": "assert_visible",
                    "target": {"strategy": "role", "role": "heading", "name": "Heading"},
                    "assertion": {"kind": "visible", "expected": True},
                    "requires_approval": False,
                },
            ],
            "assumptions": [],
            "success_criteria": ["done"],
            "warnings": [],
        }
    )
    with pytest.raises(ValueError):
        job(
            plan=plan,
            limits=Limits(
                max_duration_seconds=30,
                max_actions=1,
                memory_bytes=512 * 1024 * 1024,
                cpus=1,
                pids=32,
                artifact_bytes=100,
            ),
        )


def test_runner_checks_every_resolved_ip_and_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve_public(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        return [(None, None, None, None, ("93.184.216.34", 443))]

    monkeypatch.setattr("agentproof_runner.main.socket.getaddrinfo", resolve_public)
    assert _host_allowed("https://example.com/", ["https://example.com"])
    assert not _host_allowed("https://decoy.example/", ["https://example.com"])
    assert not _host_allowed("https://example.com:invalid/", ["https://example.com"])

    def resolve_rebinding(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        return [
            (None, None, None, None, ("93.184.216.34", 443)),
            (None, None, None, None, ("169.254.169.254", 443)),
        ]

    monkeypatch.setattr("agentproof_runner.main.socket.getaddrinfo", resolve_rebinding)
    assert not _host_allowed("https://example.com/", ["https://example.com"])


@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/",
        "https://0177.0.0.1/",
        "https://0x7f000001/",
        "https://[::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://user@owned.example/",
        "https://owned.example@169.254.169.254/",
        "https://owned.example./",
        "https://%31%32%37.0.0.1/",
    ],
)
def test_runner_denies_alternate_host_encodings_without_external_dns(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    from urllib.parse import urlsplit

    def loopback(*args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        return [(None, None, None, None, ("127.0.0.1", 443))]

    monkeypatch.setattr("agentproof_runner.main.socket.getaddrinfo", loopback)
    parsed = urlsplit(url)
    assert not _host_allowed(url, [f"{parsed.scheme}://{parsed.netloc}"])


def test_runner_blocks_disallowed_paths() -> None:
    assert _path_allowed("https://example.com/login", ["/login", "/dashboard*"])
    assert not _path_allowed("https://example.com/admin", ["/login", "/dashboard*"])


def broker_config(key: str = "x" * 48) -> dict[str, str]:
    return {
        "key": key,
        "state": ":memory:",
        "image": "registry.example/runner@sha256:" + "b" * 64,
        "network": "none",
        "docker": "docker",
        "max_concurrent": "1",
    }


def test_broker_refuses_routable_network_without_verified_gateway() -> None:
    config = broker_config()
    config["network"] = "agentproof-egress-unverified"
    with pytest.raises(RuntimeError, match="networking is disabled"):
        Broker(config)


def test_broker_accepts_operator_allowlisted_fixture_network() -> None:
    config = broker_config()
    config["network"] = "agentproof_fixture_net"
    config["network_allowlist"] = "agentproof_fixture_net"
    broker = Broker(config)
    assert broker.config["network"] == "agentproof_fixture_net"


def test_broker_rejects_add_host_without_ipv4() -> None:
    config = broker_config()
    config["add_hosts"] = "evil.example:not-an-ip"
    with pytest.raises(RuntimeError, match="BROKER_RUNNER_ADD_HOSTS"):
        Broker(config)


@pytest.mark.asyncio
async def test_emergency_stop_requests_cleanup_for_every_active_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = Broker(broker_config())
    first, second = job(), job()
    for payload in (first, second):
        broker.db.execute(
            "INSERT INTO sandboxes(job_id,run_id,sandbox_id,nonce_hash,request_hash,status,"
            "created_at,deadline) VALUES(?,?,?,?,?,?,?,?)",
            (
                str(payload.job_id),
                str(payload.run_id),
                "sbx_" + payload.job_id.hex,
                payload.nonce,
                "f" * 64,
                "running",
                datetime.now(UTC).isoformat(),
                time.time() + 60,
            ),
        )
    broker.db.commit()
    stopped: list[str] = []

    async def cancel(sandbox_id: str) -> dict[str, str]:
        stopped.append(sandbox_id)
        return {"status": "cancelled"}

    monkeypatch.setattr(broker, "cancel", cancel)
    result = await broker.emergency_stop()
    assert result["requested"] == result["cleaned"] == 2
    assert set(stopped) == {"sbx_" + first.job_id.hex, "sbx_" + second.job_id.hex}
    blocked = job()
    with pytest.raises(HTTPException, match="BROKER_EMERGENCY_STOP"):
        await broker.create(
            JobEnvelope(job=blocked, signature=_signature(broker.config["key"], blocked))
        )
    with pytest.raises(HTTPException, match="ACTIVE_SANDBOXES_REMAIN"):
        broker.resume_after_emergency_stop()
    broker.db.execute("UPDATE sandboxes SET status='cancelled'")
    broker.db.commit()
    assert broker.resume_after_emergency_stop() == {"status": "resumed"}
    assert (
        broker.db.execute(
            "SELECT value FROM broker_settings WHERE key='emergency_stop'"
        ).fetchone()["value"]
        == "0"
    )


@pytest.mark.asyncio
async def test_signed_job_is_idempotent_and_nonce_cannot_be_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = broker_config()
    broker = Broker(config)
    launches: list[str] = []

    async def launch(payload: SandboxJob, sandbox_id: str) -> None:
        launches.append(sandbox_id)
        broker.db.execute(
            "UPDATE sandboxes SET status='running',container_id=? WHERE sandbox_id=?",
            ("a" * 64, sandbox_id),
        )
        broker.db.commit()

    monkeypatch.setattr(broker, "_launch", launch)
    payload = job()
    envelope = JobEnvelope(job=payload, signature=_signature(config["key"], payload))
    first = await broker.create(envelope)
    replay = await broker.create(envelope)
    assert first.sandbox_id == replay.sandbox_id
    assert replay.idempotent is True
    assert launches == [first.sandbox_id]
    assert broker.events(first.sandbox_id, 0)["events"][0]["event"] == "sandbox.created"
    concurrent = job()
    with pytest.raises(HTTPException, match="BROKER_CONCURRENCY_LIMIT"):
        await broker.create(
            JobEnvelope(job=concurrent, signature=_signature(config["key"], concurrent))
        )

    docker_calls: list[list[str]] = []

    async def docker(
        args: list[str], *, check: bool = True, timeout_seconds: float = 15
    ) -> subprocess.CompletedProcess[str]:
        del check, timeout_seconds
        docker_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(broker, "_docker", docker)

    async def collect(_sandbox_id: str, _container_id: str) -> int:
        return 0

    monkeypatch.setattr(broker, "_collect_artifacts", collect)
    cancelled = await broker.cancel(first.sandbox_id)
    assert cancelled["status"] == "cancelled"
    assert [call[0] for call in docker_calls] == ["kill", "rm"]

    reused_nonce = job(run_id=uuid4(), job_id=uuid4(), nonce=payload.nonce)
    with pytest.raises(HTTPException, match="NONCE_REPLAY"):
        await broker.create(
            JobEnvelope(job=reused_nonce, signature=_signature(config["key"], reused_nonce))
        )
    await broker.close()


@pytest.mark.asyncio
async def test_job_rejects_modified_signature() -> None:
    config = broker_config("y" * 48)
    config["image"] = "registry.example/runner@sha256:" + "c" * 64
    broker = Broker(config)
    payload = job()
    with pytest.raises(HTTPException, match="BROKER_AUTHENTICATION_FAILED"):
        await broker.create(JobEnvelope(job=payload, signature="0" * 64))
    await broker.close()


@pytest.mark.asyncio
async def test_broker_request_auth_binds_timestamp_method_path_and_query() -> None:
    key = "k" * 48
    timestamp = str(int(time.time()))
    app = FastAPI()

    async def request_for(path: str, query: bytes = b"") -> Request:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8090),
            "app": app,
        }

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        return Request(scope, receive)

    app.state.config = {"key": key}
    path, query = "/internal/v1/sandboxes/sbx_test/status", b"after_id=5"
    signed = f"{timestamp}.GET.{path}?after_id=5.".encode()
    signature = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
    await authenticated(await request_for(path, query), signature, timestamp)

    with pytest.raises(HTTPException, match="BROKER_AUTHENTICATION_FAILED"):
        await authenticated(
            await request_for("/internal/v1/sandboxes/sbx_other/status", query),
            signature,
            timestamp,
        )
