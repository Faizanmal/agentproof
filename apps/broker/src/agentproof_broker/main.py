"""Authenticated broker. Docker CLI access is confined to this process."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BODY_BYTES = 128_000
MAX_DURATION = 600
MAX_ACTIONS = 50
MAX_MEMORY = 1_073_741_824
MAX_CPUS = 1.0
MAX_PIDS = 128
MAX_ARTIFACT_BYTES = 26_214_400
NONCE_TTL = 900
SENSITIVE_NAMES = re.compile(r"password|token|secret|authorization|cookie", re.I)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy: Literal["role", "label", "placeholder", "test_id", "text", "css"]
    role: (
        Literal[
            "button",
            "link",
            "textbox",
            "heading",
            "checkbox",
            "radio",
            "combobox",
            "option",
            "list",
            "listitem",
            "dialog",
            "alert",
            "navigation",
            "main",
            "tab",
        ]
        | None
    ) = None
    name: str = Field(min_length=1, max_length=200)
    require_unique: Literal[True] = True
    nth: int | None = Field(default=None, ge=0, le=9)

    @model_validator(mode="after")
    def locator_is_stable(self) -> Target:
        if (self.strategy == "role") != (self.role is not None):
            raise ValueError("role only belongs to role locators")
        if self.strategy == "css" and not re.fullmatch(
            r'body|main|h[1-3]|#[a-zA-Z][a-zA-Z0-9_-]{0,78}|\[data-testid="[a-zA-Z0-9_-]{1,80}"\]',
            self.name,
        ):
            raise ValueError("CSS selector is outside the limited stable-selector set")
        return self


class Assertion(Strict):
    kind: Literal["visible", "text", "url", "count"]
    expected: str | bool | int


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^s[1-9][0-9]?$", max_length=3)
    action: Literal[
        "navigate",
        "click",
        "fill",
        "select",
        "check",
        "uncheck",
        "wait_for",
        "assert_visible",
        "assert_text",
        "assert_url",
        "assert_count",
        "screenshot",
    ]
    url_path: str | None = Field(default=None, max_length=2048)
    target: Target | None = None
    value_ref: (
        Literal[
            "credential:email",
            "credential:username",
            "credential:password",
            "synthetic:email",
            "synthetic:name",
            "synthetic:text",
            "synthetic:option",
            "synthetic:yearly",
        ]
        | None
    ) = None
    assertion: Assertion | None = None
    requires_approval: bool
    timeout_ms: int = Field(default=5000, ge=100, le=10000)

    @field_validator("url_path")
    @classmethod
    def relative_only(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith("/")
            or value.startswith("//")
            or ":" in value
            or "\\" in value
            or "?" in value
            or "#" in value
            or "%" in value
            or any(p in {".", ".."} for p in value.split("/"))
        ):
            raise ValueError("navigation path must be a plain relative path")
        return value

    @model_validator(mode="after")
    def step_shape(self) -> Step:
        if self.action == "navigate":
            if self.url_path is None or self.target is not None:
                raise ValueError("navigate requires only a relative path")
        elif self.url_path is not None:
            raise ValueError("only navigate accepts url_path")
        if self.action not in {"navigate", "assert_url", "screenshot"} and self.target is None:
            raise ValueError("action requires a target")
        if (self.action in {"fill", "select"}) != (self.value_ref is not None):
            raise ValueError("fill/select require a symbolic value reference")
        expected = {
            "assert_visible": "visible",
            "assert_text": "text",
            "assert_url": "url",
            "assert_count": "count",
        }
        if self.action in expected and (
            self.assertion is None or self.assertion.kind != expected[self.action]
        ):
            raise ValueError("assertion shape does not match action")
        if self.action not in expected and self.assertion is not None:
            raise ValueError("unexpected assertion")
        return self


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"]
    goal: str = Field(min_length=1, max_length=4000)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    steps: list[Step] = Field(min_length=1, max_length=15)
    assumptions: list[str] = Field(max_length=15)
    success_criteria: list[str] = Field(min_length=1, max_length=15)
    warnings: list[str] = Field(max_length=15)

    @model_validator(mode="after")
    def valid_steps(self) -> Plan:
        if self.steps[0].action != "navigate" or len({step.id for step in self.steps}) != len(
            self.steps
        ):
            raise ValueError("plan must start with a navigation and have unique step IDs")
        if not any(step.action.startswith("assert_") for step in self.steps):
            raise ValueError("plan must contain an assertion")
        return self


class Limits(Strict):
    max_duration_seconds: int = Field(ge=1, le=MAX_DURATION)
    max_actions: int = Field(ge=1, le=MAX_ACTIONS)
    memory_bytes: int = Field(ge=256 * 1024 * 1024, le=MAX_MEMORY)
    cpus: float = Field(gt=0, le=MAX_CPUS)
    pids: int = Field(ge=16, le=MAX_PIDS)
    artifact_bytes: int = Field(ge=1, le=MAX_ARTIFACT_BYTES)


class SandboxJob(Strict):
    job_id: UUID
    run_id: UUID
    project_id: UUID
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_origins: list[str] = Field(min_length=1, max_length=10)
    allowed_paths: list[str] = Field(min_length=1, max_length=20)
    allowed_methods: list[Literal["GET", "HEAD", "POST"]] = Field(
        default=["GET", "HEAD"], min_length=1, max_length=3
    )
    max_pages: int = Field(default=10, ge=1, le=50)
    max_redirects: int = Field(default=3, ge=0, le=5)
    plan: Plan
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    limits: Limits
    authorization_expires_at: datetime
    expires_at: datetime
    nonce: str = Field(min_length=32, max_length=128)

    @field_validator("expires_at", "authorization_expires_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def scope_is_restricted(self) -> SandboxJob:
        if len(set(self.approved_origins)) != len(self.approved_origins):
            raise ValueError("duplicate origins")
        for origin in self.approved_origins:
            from urllib.parse import urlsplit

            parsed = urlsplit(origin)
            try:
                port = parsed.port
            except ValueError:
                raise ValueError("origin port is invalid") from None
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
                or parsed.netloc != parsed.netloc.lower()
                or origin != f"https://{parsed.netloc}"
                or parsed.hostname.endswith(".")
                or port == 0
            ):
                raise ValueError("only explicitly authorized HTTPS origins may run")
            try:
                ipaddress.ip_address(parsed.hostname)
            except ValueError:
                try:
                    if parsed.hostname.encode("idna").decode("ascii").lower() != parsed.hostname:
                        raise ValueError("origin host must be canonical IDNA")
                except UnicodeError:
                    raise ValueError("invalid origin host") from None
            else:
                raise ValueError("IP literal origins are not allowed")
            if re.fullmatch(
                r"(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+))*",
                parsed.hostname,
            ):
                raise ValueError("alternate numeric host encodings are not allowed")
            expected_host = parsed.hostname
            if port is not None and port != 443:
                expected_host += f":{port}"
            if parsed.netloc != expected_host:
                raise ValueError("origin authority is not normalized")
            if parsed.hostname in {
                "localhost",
                "metadata.google.internal",
            } or parsed.hostname.endswith((".localhost", ".local", ".internal")):
                raise ValueError("local and metadata hostnames are not allowed")
        if not self.allowed_paths or any(
            not path.startswith("/")
            or path.startswith("//")
            or ".." in path.split("/")
            or "\\" in path
            or "?" in path
            or "#" in path
            or "%" in path
            for path in self.allowed_paths
        ):
            raise ValueError("invalid allowed path patterns")
        if len(self.plan.steps) > self.limits.max_actions:
            raise ValueError("plan exceeds broker action limits")
        if self.plan.risk_level == "HIGH" and not any(s.requires_approval for s in self.plan.steps):
            raise ValueError("HIGH-risk plan lacks step approval evidence")
        return self


class JobEnvelope(Strict):
    job: SandboxJob
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class SandboxAccepted(Strict):
    sandbox_id: str
    run_id: UUID
    status: Literal["starting", "running", "completed", "failed", "cancelled", "timed_out"]
    idempotent: bool = False


def _configuration() -> dict[str, str]:
    return {
        "key": os.environ.get("BROKER_HMAC_KEY", ""),
        "state": os.environ.get("BROKER_STATE_PATH", "/var/lib/agentproof-broker/state.sqlite3"),
        "image": os.environ.get("BROKER_RUNNER_IMAGE_DIGEST", ""),
        "network": os.environ.get("BROKER_SANDBOX_NETWORK", "none"),
        "network_allowlist": os.environ.get("BROKER_SANDBOX_NETWORK_ALLOWLIST", ""),
        "add_hosts": os.environ.get("BROKER_RUNNER_ADD_HOSTS", ""),
        "docker": os.environ.get("DOCKER_BIN", "docker"),
        "max_concurrent": os.environ.get("BROKER_MAX_CONCURRENT", "1"),
        "artifact_root": os.environ.get(
            "BROKER_ARTIFACT_PATH", "/var/lib/agentproof-broker/artifacts"
        ),
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _signature(key: str, job: SandboxJob) -> str:
    return hmac.new(
        key.encode(), _canonical(job.model_dump(mode="json")), hashlib.sha256
    ).hexdigest()


def _validate_image(image: str) -> None:
    # Refuse tags and locally mutable names. The operator must deploy an inspected digest.
    # Local Engine image ids (`sha256:<64 hex>`) are also accepted with --pull=never.
    if not re.fullmatch(
        r"(?:[A-Za-z0-9./_-]+@)?sha256:[0-9a-f]{64}",
        image,
    ):
        raise RuntimeError("BROKER_RUNNER_IMAGE_DIGEST must be an immutable sha256 image digest")


def _allowed_sandbox_networks(raw: str) -> set[str]:
    """Operator allowlist in addition to the safe default `none`.

    Names must be exact Docker network identifiers (lowercase alnum/._-).
    Arbitrary or empty entries are ignored. This is not a packet-tested gateway.
    """
    allowed = {"none"}
    for part in raw.split(","):
        name = part.strip()
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", name):
            allowed.add(name)
    return allowed


def _runner_add_hosts(raw: str) -> list[str]:
    """Optional host:ip pairs injected as docker --add-host (fixture DNS pinning)."""
    hosts: list[str] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if not re.fullmatch(
            r"[A-Za-z0-9._-]+:(?:\d{1,3}\.){3}\d{1,3}",
            item,
        ):
            raise RuntimeError("BROKER_RUNNER_ADD_HOSTS entries must be hostname:ipv4")
        hosts.append(item)
    return hosts


class Broker:
    def __init__(self, config: dict[str, str]) -> None:
        if len(config["key"]) < 32:
            raise RuntimeError("BROKER_HMAC_KEY must contain at least 32 characters")
        _validate_image(config["image"])
        # Default remains network=none. A named network is accepted only when the
        # operator explicitly allowlists it (fixture-only Track A). Packet-level
        # gateway verification is still required before public multi-tenant use.
        allowed = _allowed_sandbox_networks(config.get("network_allowlist", ""))
        if config["network"] not in allowed:
            raise RuntimeError(
                "sandbox networking is disabled until the dedicated egress gateway is verified"
            )
        self.add_hosts = _runner_add_hosts(config.get("add_hosts", ""))
        self.config = config
        self.artifact_root = Path(
            config.get("artifact_root", "./.state/broker-artifacts")
        ).resolve()
        state_path = Path(config["state"])
        state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(config["state"], check_same_thread=False)
        if os.name != "nt" and config["state"] != ":memory:":
            state_path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sandboxes (
                job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
                sandbox_id TEXT NOT NULL UNIQUE,
                nonce_hash TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, status TEXT NOT NULL,
                container_id TEXT, created_at TEXT NOT NULL, deadline REAL NOT NULL,
                events TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS nonces (nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS broker_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, sandbox_id TEXT,
                event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS broker_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO broker_settings(key,value) VALUES ('emergency_stop','0');
        """)
        self.db.commit()
        self.lock = asyncio.Lock()
        self.max_concurrent = min(8, max(1, int(config["max_concurrent"])))
        self.watch_tasks: set[asyncio.Task[None]] = set()

    async def startup_reconcile(self) -> None:
        # Conservative restart policy: stop every labeled runner unless the durable lease can
        # be positively revalidated. Orphan containers never survive a broker restart.
        result = await self._docker(["ps", "-aq", "--filter", "label=agentproof.managed=true"])
        for container_id in result.stdout.splitlines():
            await self._docker(["rm", "-f", container_id], check=False)
        self._event(
            None, None, "sandbox.reconciled", {"killed_orphans": len(result.stdout.splitlines())}
        )
        self.db.execute(
            "UPDATE sandboxes SET status='failed' WHERE status IN ('starting','running')"
        )
        self.db.commit()

    async def close(self) -> None:
        # Graceful broker shutdown kills owned containers before the engine handle is closed.
        # A hard process/VM crash is handled by startup_reconcile on the next boot.
        try:
            result = await self._docker(
                ["ps", "-aq", "--filter", "label=agentproof.managed=true"],
                check=False,
            )
            for container_id in result.stdout.splitlines():
                await self._docker(["rm", "-f", container_id], check=False)
        except Exception:
            pass
        if self.watch_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.watch_tasks, return_exceptions=True), timeout=5
                )
            except TimeoutError:
                pass
        self.db.execute(
            "UPDATE sandboxes SET status='failed' WHERE status IN ('starting','running')"
        )
        self.db.commit()
        self.db.close()

    async def _docker(
        self, args: list[str], *, check: bool = True, timeout_seconds: float = 15
    ) -> subprocess.CompletedProcess[str]:
        if shutil.which(self.config["docker"]) is None:
            raise RuntimeError("Docker CLI is unavailable")
        proc = await asyncio.create_subprocess_exec(
            self.config["docker"],
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if proc.stdout is None or proc.stderr is None:
            raise RuntimeError("Docker process pipes were not created")

        async def bounded(stream: asyncio.StreamReader, limit: int) -> bytes:
            captured = bytearray()
            while chunk := await stream.read(4096):
                if len(captured) < limit:
                    captured.extend(chunk[: limit - len(captured)])
            return bytes(captured)

        try:
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(
                    bounded(proc.stdout, 4096),
                    bounded(proc.stderr, 1024),
                    proc.wait(),
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(504, "SANDBOX_RUNTIME_TIMEOUT") from None
        output = stdout.decode(errors="replace")
        error = stderr.decode(errors="replace")
        if check and proc.returncode:
            # Keep detail short and non-sensitive; full stderr stays in process logs.
            snippet = " ".join(error.strip().split())[:180]
            import sys

            print(
                f"docker_failed code={proc.returncode} args={args[:12]} err={snippet}",
                file=sys.stderr,
                flush=True,
            )
            raise HTTPException(503, "SANDBOX_RUNTIME_UNAVAILABLE")
        return subprocess.CompletedProcess(
            [self.config["docker"], *args], proc.returncode or 0, output, error
        )

    async def create(
        self, envelope: JobEnvelope, *, raw_job: dict[str, Any] | None = None
    ) -> SandboxAccepted:
        job = envelope.job
        # Verify over the wire JSON (raw_job) when provided. Signing after
        # model_dump(mode="json") rewrites datetimes and breaks worker HMACs.
        signed_payload = raw_job if raw_job is not None else job.model_dump(mode="json")
        canonical = _canonical(signed_payload)
        expected = hmac.new(
            self.config["key"].encode(), canonical, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, envelope.signature):
            raise HTTPException(403, "BROKER_AUTHENTICATION_FAILED")
        now = datetime.now(UTC)
        if job.expires_at <= now or (job.expires_at - now).total_seconds() > 120:
            raise HTTPException(403, "JOB_EXPIRED_OR_TOO_LONG")
        if job.authorization_expires_at <= now:
            raise HTTPException(403, "AUTHORIZATION_EXPIRED")
        plan_for_digest = (
            signed_payload.get("plan")
            if isinstance(signed_payload, dict)
            else job.plan.model_dump(mode="json")
        )
        plan_digest = hashlib.sha256(_canonical(plan_for_digest)).hexdigest()
        if not hmac.compare_digest(plan_digest, job.plan_digest):
            raise HTTPException(403, "PLAN_DIGEST_MISMATCH")
        request_hash = hashlib.sha256(canonical).hexdigest()
        nonce_hash = hashlib.sha256(job.nonce.encode()).hexdigest()
        async with self.lock:
            previous = self.db.execute(
                "SELECT * FROM sandboxes WHERE job_id=?", (str(job.job_id),)
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise HTTPException(409, "JOB_ID_REUSE")
                return SandboxAccepted(
                    sandbox_id=previous["sandbox_id"],
                    run_id=job.run_id,
                    status=previous["status"],
                    idempotent=True,
                )
            stopped = self.db.execute(
                "SELECT value FROM broker_settings WHERE key='emergency_stop'"
            ).fetchone()
            if stopped is not None and stopped["value"] == "1":
                raise HTTPException(503, "BROKER_EMERGENCY_STOP")
            existing_run = self.db.execute(
                "SELECT 1 FROM sandboxes WHERE run_id=?", (str(job.run_id),)
            ).fetchone()
            if existing_run:
                raise HTTPException(409, "RUN_ALREADY_ASSIGNED")
            active_count = self.db.execute(
                "SELECT count(*) FROM sandboxes WHERE status IN ('starting','running')"
            ).fetchone()[0]
            if active_count >= self.max_concurrent:
                raise HTTPException(429, "BROKER_CONCURRENCY_LIMIT")
            self.db.execute("DELETE FROM nonces WHERE expires < ?", (time.time(),))
            if self.db.execute("SELECT 1 FROM nonces WHERE nonce_hash=?", (nonce_hash,)).fetchone():
                raise HTTPException(409, "NONCE_REPLAY")
            sandbox_id = f"sbx_{uuid4().hex}"
            deadline = min(
                job.authorization_expires_at.timestamp(),
                time.time() + job.limits.max_duration_seconds,
            )
            self.db.execute(
                "INSERT INTO nonces VALUES (?,?)", (nonce_hash, time.time() + NONCE_TTL)
            )
            self.db.execute(
                "INSERT INTO sandboxes(job_id,run_id,sandbox_id,nonce_hash,request_hash,"
                "status,created_at,deadline) VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(job.job_id),
                    str(job.run_id),
                    sandbox_id,
                    nonce_hash,
                    request_hash,
                    "starting",
                    now.isoformat(),
                    deadline,
                ),
            )
            self.db.commit()
            self._event(str(job.run_id), sandbox_id, "sandbox.created", {"status": "starting"})
        async with self.lock:
            current = self.db.execute(
                "SELECT status FROM sandboxes WHERE sandbox_id=?", (sandbox_id,)
            ).fetchone()
            if current is None or current["status"] != "starting":
                return SandboxAccepted(sandbox_id=sandbox_id, run_id=job.run_id, status="cancelled")
            try:
                await self._launch(job, sandbox_id)
            except Exception:
                self.db.execute(
                    "UPDATE sandboxes SET status='failed' WHERE sandbox_id=? AND status='starting'",
                    (sandbox_id,),
                )
                self.db.commit()
                self._event(str(job.run_id), sandbox_id, "sandbox.terminated", {"status": "failed"})
                raise
        return SandboxAccepted(sandbox_id=sandbox_id, run_id=job.run_id, status="running")

    async def _launch(self, job: SandboxJob, sandbox_id: str) -> None:
        container = f"agentproof-{sandbox_id[4:]}"
        limits = job.limits
        manifest = {
            "schema_version": "1.0",
            "sandbox_id": sandbox_id,
            "run_id": str(job.run_id),
            "origins": job.approved_origins,
            "allowed_paths": job.allowed_paths,
            "allowed_methods": job.allowed_methods,
            "max_pages": job.max_pages,
            "max_redirects": job.max_redirects,
            "max_actions": min(job.limits.max_actions, MAX_ACTIONS),
            "max_duration_seconds": min(job.limits.max_duration_seconds, MAX_DURATION),
            "max_artifact_bytes": min(job.limits.artifact_bytes, MAX_ARTIFACT_BYTES),
            "plan": job.plan.model_dump(mode="json"),
        }
        # Bind-mount the manifest: docker cp cannot write into a --read-only rootfs.
        # Keep the file on durable broker storage for the container lifetime.
        manifest_dir = self.artifact_root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        path = manifest_dir / f"{sandbox_id}.json"
        path.write_bytes(_canonical(manifest))
        path.chmod(0o644)
        args = [
            "create",
            "--name",
            container,
            "--label",
            "agentproof.managed=true",
            "--label",
            f"agentproof.sandbox={sandbox_id}",
            "--label",
            f"agentproof.run={job.run_id}",
            "--pull=never",
            "--network",
            self.config["network"],
            "--init",
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
                "--log-driver",
                "local",
                "--log-opt",
                "max-size=1m",
                "--log-opt",
                "max-file=2",
                "--log-opt",
                "compress=false",
            "--pids-limit",
            str(limits.pids),
            "--memory",
            str(limits.memory_bytes),
            "--memory-swap",
            str(limits.memory_bytes),
            "--cpus",
            str(limits.cpus),
            "--shm-size",
            "128m",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m,uid=10001,gid=10001",
            "--mount",
            f"type=bind,source={path},target=/tmp/agentproof-manifest.json,readonly",
        ]
        for host_map in self.add_hosts:
            args.extend(["--add-host", host_map])
        args.append(self.config["image"])
        created = await self._docker(args, timeout_seconds=30)
        container_id = created.stdout.strip()[:128]
        if not re.fullmatch(r"[a-f0-9]{12,64}", container_id):
            path.unlink(missing_ok=True)
            raise HTTPException(503, "SANDBOX_RUNTIME_INVALID_RESPONSE")
        try:
            await self._docker(["start", container_id])
        except Exception:
            await self._docker(["rm", "-f", container_id], check=False)
            path.unlink(missing_ok=True)
            self.db.execute(
                "UPDATE sandboxes SET status='failed' WHERE sandbox_id=?", (sandbox_id,)
            )
            self.db.commit()
            raise
        self.db.execute(
            "UPDATE sandboxes SET status='running',container_id=? WHERE sandbox_id=?",
            (container_id, sandbox_id),
        )
        self.db.commit()
        task = asyncio.create_task(
            self._watch(sandbox_id, container_id, job.limits.max_duration_seconds)
        )
        self.watch_tasks.add(task)
        task.add_done_callback(self.watch_tasks.discard)

    async def _watch(self, sandbox_id: str, container_id: str, duration: int) -> None:
        try:
            result = await self._docker(["wait", container_id], timeout_seconds=duration + 5)
            code = result.stdout.strip()
            status = "completed" if code == "0" else "failed"
        except HTTPException:
            await self._docker(["kill", container_id], check=False)
            status = "timed_out"
        logs = await self._docker(
            ["logs", "--tail", "100", container_id], check=False, timeout_seconds=5
        )
        row = self.db.execute(
            "SELECT run_id,status FROM sandboxes WHERE sandbox_id=?", (sandbox_id,)
        ).fetchone()
        run_id = row["run_id"] if row else None
        self._capture_runner_events(run_id, sandbox_id, logs.stdout)
        try:
            await self._collect_artifacts(sandbox_id, container_id)
        except Exception:
            self._event(
                run_id,
                sandbox_id,
                "sandbox.artifact_collection_failed",
                {"reason": "COLLECTION_FAILED"},
            )
        await self._docker(["rm", "-f", container_id], check=False)
        if row is not None and row["status"] == "cancelled":
            status = "cancelled"
        async with self.lock:
            current = self.db.execute(
                "SELECT status FROM sandboxes WHERE sandbox_id=?", (sandbox_id,)
            ).fetchone()
            if current is not None and current["status"] == "cancelled":
                status = "cancelled"
            self.db.execute(
                "UPDATE sandboxes SET status=? WHERE sandbox_id=?", (status, sandbox_id)
            )
            self.db.commit()
            self._event(run_id, sandbox_id, "sandbox.terminated", {"status": status})
            self._event(run_id, sandbox_id, "sandbox.cleaned_up", {"container_removed": True})

    async def _collect_artifacts(self, sandbox_id: str, container_id: str) -> int:
        if re.fullmatch(r"sbx_[a-f0-9]{32}", sandbox_id) is None:
            raise ValueError("invalid sandbox artifact scope")
        self.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self.artifact_root / sandbox_id
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self.artifact_root.chmod(0o700)
            destination.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix="agentproof-artifact-copy-") as staging:
            await self._docker(
                ["cp", f"{container_id}:/tmp/agentproof-artifacts/.", staging],
                check=False,
                timeout_seconds=30,
            )
            total, count = 0, 0
            paths = await asyncio.to_thread(lambda: list(Path(staging).rglob("*")))
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    continue
                name = path.name
                if re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(?:png|webp|json)", name) is None:
                    continue
                size = path.stat().st_size
                if size <= 0 or size > 26_214_400:
                    continue
                total += size
                count += 1
                if total > 104_857_600 or count > 100:
                    raise ValueError("artifact bundle exceeds configured limits")
                target = destination / name
                staged = destination / f".{uuid4().hex}.tmp"
                shutil.copyfile(path, staged)
                if os.name != "nt":
                    staged.chmod(0o600)
                staged.replace(target)
            return count

    def artifact_bundle(self, sandbox_id: str) -> bytes:
        if re.fullmatch(r"sbx_[a-f0-9]{32}", sandbox_id) is None:
            raise HTTPException(404, "SANDBOX_NOT_FOUND")
        root = self.artifact_root / sandbox_id
        if not root.is_dir() or root.is_symlink():
            raise HTTPException(404, "ARTIFACTS_UNAVAILABLE")
        output = BytesIO()
        total = 0
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.iterdir()):
                if path.is_symlink() or not path.is_file():
                    continue
                if re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(?:png|webp|json)", path.name) is None:
                    continue
                content = path.read_bytes()
                total += len(content)
                if total > 104_857_600:
                    raise HTTPException(413, "ARTIFACT_BUNDLE_TOO_LARGE")
                archive.writestr(path.name, content)
        return output.getvalue()

    def status(self, sandbox_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM sandboxes WHERE sandbox_id=?", (sandbox_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "SANDBOX_NOT_FOUND")
        return {
            "sandbox_id": sandbox_id,
            "run_id": row["run_id"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    async def cancel(self, sandbox_id: str) -> dict[str, Any]:
        async with self.lock:
            row = self.db.execute(
                "SELECT * FROM sandboxes WHERE sandbox_id=?", (sandbox_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(404, "SANDBOX_NOT_FOUND")
            if row["status"] not in {"starting", "running"}:
                return self.status(sandbox_id)
            if row["container_id"] and row["status"] in {"starting", "running"}:
                await self._docker(["kill", row["container_id"]], check=False)
                try:
                    await self._collect_artifacts(sandbox_id, row["container_id"])
                except Exception:
                    self._event(
                        row["run_id"],
                        sandbox_id,
                        "sandbox.artifact_collection_failed",
                        {"reason": "COLLECTION_FAILED"},
                    )
                await self._docker(["rm", "-f", row["container_id"]], check=False)
            self.db.execute(
                "UPDATE sandboxes SET status='cancelled' WHERE sandbox_id=?", (sandbox_id,)
            )
            self.db.commit()
            self._event(row["run_id"], sandbox_id, "sandbox.terminated", {"status": "cancelled"})
            self._event(
                row["run_id"], sandbox_id, "sandbox.cleaned_up", {"container_removed": True}
            )
        return self.status(sandbox_id)

    async def emergency_stop(self) -> dict[str, Any]:
        """Terminate every active lease on this dedicated broker engine."""
        async with self.lock:
            self.db.execute("UPDATE broker_settings SET value='1' WHERE key='emergency_stop'")
            rows = self.db.execute(
                "SELECT sandbox_id FROM sandboxes WHERE status IN ('starting','running')"
            ).fetchall()
            self.db.commit()
        outcomes: list[dict[str, str]] = []
        for row in rows:
            sandbox_id = str(row["sandbox_id"])
            try:
                status = await self.cancel(sandbox_id)
                outcomes.append({"sandbox_id": sandbox_id, "status": status["status"]})
            except Exception:
                outcomes.append({"sandbox_id": sandbox_id, "status": "cleanup_failed"})
        return {
            "requested": len(rows),
            "cleaned": sum(item["status"] == "cancelled" for item in outcomes),
            "outcomes": outcomes,
        }

    def resume_after_emergency_stop(self) -> dict[str, str]:
        active = self.db.execute(
            "SELECT count(*) FROM sandboxes WHERE status IN ('starting','running')"
        ).fetchone()[0]
        if active:
            raise HTTPException(409, "ACTIVE_SANDBOXES_REMAIN")
        self.db.execute("UPDATE broker_settings SET value='0' WHERE key='emergency_stop'")
        self.db.commit()
        return {"status": "resumed"}

    def heartbeat(self, sandbox_id: str, run_id: UUID) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM sandboxes WHERE sandbox_id=? AND run_id=?", (sandbox_id, str(run_id))
        ).fetchone()
        if row is None or row["status"] != "running" or row["deadline"] <= time.time():
            raise HTTPException(409, "SANDBOX_LEASE_ENDED")
        return self.status(sandbox_id)

    def _event(
        self, run_id: str | None, sandbox_id: str | None, event_type: str, payload: dict[str, Any]
    ) -> None:
        permitted = {
            "status",
            "container_removed",
            "killed_orphans",
            "reason",
            "method",
            "url_path",
            "step_id",
            "action",
            "sequence",
            "page_url",
            "assertion",
            "verdict",
            "steps",
            "elapsed_ms",
            "artifact_id",
            "mime_type",
            "bytes",
            "sha256",
            "code",
            "uid",
        }
        safe = {key: value for key, value in payload.items() if key in permitted}
        self.db.execute(
            "INSERT INTO broker_events(run_id,sandbox_id,event_type,payload,created_at) "
            "VALUES(?,?,?,?,?)",
            (
                run_id or "broker",
                sandbox_id,
                event_type,
                json.dumps(safe, separators=(",", ":")),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.db.commit()

    def _capture_runner_events(self, run_id: str | None, sandbox_id: str, output: str) -> None:
        permitted = {
            "sandbox.started": {"run_id", "sandbox_id", "uid"},
            "sandbox.step.started": {"step_id", "action", "sequence"},
            "sandbox.step": {
                "step_id",
                "action",
                "sequence",
                "status",
                "elapsed_ms",
                "assertion",
                "page_url",
            },
            "sandbox.policy_denied": {"reason", "method", "url_path"},
            "sandbox.artifact": {
                "artifact_id",
                "mime_type",
                "bytes",
                "sha256",
                "step_id",
                "sequence",
                "redaction_policy",
            },
            "sandbox.completed": {"run_id", "verdict", "steps"},
            "sandbox.failed": {"code"},
        }
        for line in output.splitlines()[:100]:
            if len(line) > 2048:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = payload.get("event") if isinstance(payload, dict) else None
            if event_type not in permitted:
                continue
            safe = {key: value for key, value in payload.items() if key in permitted[event_type]}
            self._event(run_id, sandbox_id, event_type, safe)

    def events(self, sandbox_id: str, after_id: int) -> dict[str, Any]:
        if (
            self.db.execute("SELECT 1 FROM sandboxes WHERE sandbox_id=?", (sandbox_id,)).fetchone()
            is None
        ):
            raise HTTPException(404, "SANDBOX_NOT_FOUND")
        rows = self.db.execute(
            "SELECT id,event_type,payload,created_at FROM broker_events "
            "WHERE sandbox_id=? AND id>? ORDER BY id LIMIT 100",
            (sandbox_id, max(0, after_id)),
        ).fetchall()
        return {
            "sandbox_id": sandbox_id,
            "events": [
                {
                    "id": row["id"],
                    "event": row["event_type"],
                    "data": json.loads(row["payload"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
        }


async def authenticated(
    request: Request,
    x_agentproof_signature: Annotated[str, Header()],
    x_agentproof_timestamp: Annotated[str, Header()],
) -> None:
    config = request.app.state.config
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "BROKER_BODY_TOO_LARGE")
    try:
        timestamp = int(x_agentproof_timestamp)
    except ValueError:
        raise HTTPException(403, "BROKER_AUTHENTICATION_FAILED") from None
    if abs(time.time() - timestamp) > 30:
        raise HTTPException(403, "BROKER_CLOCK_SKEW")
    signed = (
        x_agentproof_timestamp.encode()
        + b"."
        + request.method.upper().encode()
        + b"."
        + request.url.path.encode()
        + (b"?" + request.url.query.encode() if request.url.query else b"")
        + b"."
        + body
    )
    expected = hmac.new(config["key"].encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_agentproof_signature):
        raise HTTPException(403, "BROKER_AUTHENTICATION_FAILED")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = _configuration()
    broker = Broker(config)
    app.state.config, app.state.broker = config, broker
    await broker.startup_reconcile()
    try:
        yield
    finally:
        await broker.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="AgentProof private broker", docs_url=None, redoc_url=None, lifespan=lifespan
    )

    @app.post(
        "/internal/v1/sandboxes",
        response_model=SandboxAccepted,
        dependencies=[Depends(authenticated)],
    )
    async def create_sandbox(envelope: JobEnvelope, request: Request) -> SandboxAccepted:
        broker = cast(Broker, request.app.state.broker)
        raw_body = await request.body()
        try:
            raw_job = json.loads(raw_body.decode()).get("job")
        except (UnicodeError, json.JSONDecodeError, AttributeError):
            raw_job = None
        if not isinstance(raw_job, dict):
            raw_job = None
        return await broker.create(envelope, raw_job=raw_job)

    @app.get("/internal/v1/sandboxes/{sandbox_id}/status", dependencies=[Depends(authenticated)])
    async def sandbox_status(sandbox_id: str, request: Request) -> dict[str, Any]:
        broker = cast(Broker, request.app.state.broker)
        return broker.status(sandbox_id)

    @app.post("/internal/v1/sandboxes/{sandbox_id}/cancel", dependencies=[Depends(authenticated)])
    async def cancel_sandbox(sandbox_id: str, request: Request) -> dict[str, Any]:
        broker = cast(Broker, request.app.state.broker)
        return await broker.cancel(sandbox_id)

    @app.post(
        "/internal/v1/sandboxes/{sandbox_id}/heartbeat", dependencies=[Depends(authenticated)]
    )
    async def heartbeat(sandbox_id: str, request: Request, run_id: UUID) -> dict[str, Any]:
        broker = cast(Broker, request.app.state.broker)
        return broker.heartbeat(sandbox_id, run_id)

    @app.get("/internal/v1/sandboxes/{sandbox_id}/events", dependencies=[Depends(authenticated)])
    async def events(sandbox_id: str, request: Request, after_id: int = 0) -> dict[str, Any]:
        broker = cast(Broker, request.app.state.broker)
        return broker.events(sandbox_id, after_id)

    @app.get(
        "/internal/v1/sandboxes/{sandbox_id}/artifacts",
        dependencies=[Depends(authenticated)],
    )
    async def sandbox_artifacts(sandbox_id: str, request: Request) -> Response:
        broker = cast(Broker, request.app.state.broker)
        return Response(
            broker.artifact_bundle(sandbox_id),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="evidence.zip"',
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
            },
        )

    @app.post("/internal/v1/emergency-stop", dependencies=[Depends(authenticated)])
    async def emergency_stop(request: Request) -> dict[str, Any]:
        broker = cast(Broker, request.app.state.broker)
        return await broker.emergency_stop()

    @app.post("/internal/v1/resume", dependencies=[Depends(authenticated)])
    async def resume(request: Request) -> dict[str, str]:
        broker = cast(Broker, request.app.state.broker)
        return broker.resume_after_emergency_stop()

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("BROKER_BIND_HOST", "127.0.0.1")
    cert, key = os.environ.get("BROKER_TLS_CERT", ""), os.environ.get("BROKER_TLS_KEY", "")
    if host not in {"127.0.0.1", "::1", "localhost"} and (not cert or not key):
        raise RuntimeError("non-loopback broker listeners require TLS certificates")
    uvicorn.run(
        "agentproof_broker.main:create_app",
        factory=True,
        host=host,
        port=int(os.environ.get("BROKER_PORT", "8090")),
        access_log=False,
        ssl_certfile=cert or None,
        ssl_keyfile=key or None,
    )


app = create_app()
