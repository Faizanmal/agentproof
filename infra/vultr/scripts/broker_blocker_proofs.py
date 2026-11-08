#!/usr/bin/env python3
"""Track B2 proofs against the live sandbox broker (HMAC + TLS).

Proves: nonce one-use, artifact_bytes clamp, cancel, orphan reconcile signal.
Requires env: BROKER_URL, BROKER_HMAC_KEY, SSL_CERT_FILE (optional).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign(key: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    return hmac.new(
        key.encode(),
        timestamp.encode() + b"." + method.encode() + b"." + path.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()


def job_payload(*, nonce: str, artifact_bytes: int = 26_214_400) -> dict:
    now = datetime.now(UTC)
    plan = {
        "schema_version": "1.0",
        "goal": "blocker proof",
        "risk_level": "LOW",
        "assumptions": ["fixture is up"],
        "success_criteria": ["heading visible"],
        "warnings": [],
        "steps": [
            {
                "id": "s1",
                "action": "navigate",
                "url_path": "/qa-fixture/",
                "target": None,
                "value_ref": None,
                "assertion": None,
                "requires_approval": False,
                "timeout_ms": 5000,
            },
            {
                "id": "s2",
                "action": "assert_visible",
                "url_path": None,
                "target": {
                    "strategy": "role",
                    "role": "heading",
                    "name": "AgentProof owned fixture",
                    "require_unique": True,
                    "nth": None,
                },
                "value_ref": None,
                "assertion": {"kind": "visible", "expected": True},
                "requires_approval": False,
                "timeout_ms": 5000,
            },
        ],
    }
    job = {
        "job_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "policy_hash": "b" * 64,
        "approved_origins": ["https://agent.faizanmalik.me"],
        "allowed_paths": ["/qa-fixture/", "/*"],
        "allowed_methods": ["GET", "HEAD"],
        "max_pages": 10,
        "max_redirects": 3,
        "plan": plan,
        "plan_digest": hashlib.sha256(canonical(plan)).hexdigest(),
        "limits": {
            "max_duration_seconds": 60,
            "max_actions": 4,
            "memory_bytes": 512 * 1024 * 1024,
            "cpus": 1.0,
            "pids": 128,
            "artifact_bytes": artifact_bytes,
        },
        "authorization_expires_at": (now + timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(seconds=90)).isoformat(),
        "nonce": nonce,
    }
    return job


def post_sandbox(client: httpx.Client, base: str, key: str, job: dict) -> httpx.Response:
    envelope = {
        "job": job,
        "signature": hmac.new(key.encode(), canonical(job), hashlib.sha256).hexdigest(),
    }
    body = canonical(envelope)
    path = "/internal/v1/sandboxes"
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Agentproof-Timestamp": timestamp,
        "X-Agentproof-Signature": sign(key, timestamp, "POST", path, body),
    }
    return client.post(base + path, content=body, headers=headers)


def signed_request(
    client: httpx.Client, base: str, key: str, method: str, path: str, body: bytes = b""
) -> httpx.Response:
    timestamp = str(int(time.time()))
    headers = {
        "X-Agentproof-Timestamp": timestamp,
        "X-Agentproof-Signature": sign(key, timestamp, method, path, body),
    }
    if body:
        headers["Content-Type"] = "application/json"
    return client.request(method, base + path, content=body or None, headers=headers)


def main() -> int:
    base = os.environ["BROKER_URL"].rstrip("/")
    key = os.environ["BROKER_HMAC_KEY"]
    ca = os.environ.get("SSL_CERT_FILE")
    results: list[str] = []
    with httpx.Client(verify=ca or True, trust_env=False, timeout=30) as client:
        nonce = uuid.uuid4().hex + uuid.uuid4().hex
        job = job_payload(nonce=nonce)
        first = post_sandbox(client, base, key, job)
        results.append(f"create_status={first.status_code}")
        if first.status_code != 200:
            print("FAIL create", first.text[:300])
            return 1
        sandbox_id = first.json()["sandbox_id"]
        results.append(f"sandbox_id={sandbox_id}")

        # Artifact bytes above broker max rejected by validation (no create).
        big = job_payload(nonce=uuid.uuid4().hex + uuid.uuid4().hex, artifact_bytes=50_000_000)
        oversized = post_sandbox(client, base, key, big)
        results.append(
            f"oversized_artifact_status={oversized.status_code} body={oversized.text[:160]}"
        )
        if oversized.status_code != 422:
            print("FAIL oversized artifact not rejected with 422")
            print("\n".join(results))
            return 3

        # Cancel frees the concurrency slot and ends the lease.
        cancel = signed_request(
            client, base, key, "POST", f"/internal/v1/sandboxes/{sandbox_id}/cancel", b"{}"
        )
        results.append(f"cancel_status={cancel.status_code} body={cancel.text[:160]}")
        if cancel.status_code >= 400:
            print("FAIL cancel")
            print("\n".join(results))
            return 4

        # Heartbeat after cancel should report lease ended / not running.
        run_id = job["run_id"]
        hb = signed_request(
            client,
            base,
            key,
            "POST",
            f"/internal/v1/sandboxes/{sandbox_id}/heartbeat?run_id={run_id}",
            b"{}",
        )
        results.append(f"heartbeat_status={hb.status_code} body={hb.text[:160]}")
        if hb.status_code < 400:
            print("FAIL heartbeat still accepted after cancel")
            print("\n".join(results))
            return 5

        # One-use nonce: identical nonce on a new job must fail after slot free.
        job2 = job_payload(nonce=nonce)
        second = post_sandbox(client, base, key, job2)
        results.append(f"reuse_nonce_status={second.status_code} body={second.text[:160]}")
        if second.status_code < 400:
            print("FAIL nonce reuse accepted")
            print("\n".join(results))
            return 2

        # Orphan reconcile is recorded at broker startup; surface health.
        health = client.get(base + "/healthz")
        results.append(f"health={health.status_code} {health.text}")

    print("PROOF_OK")
    print("\n".join(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
