#!/usr/bin/env python3
"""Post a minimal signed sandbox job the same way the worker does."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from agentproof_api.settings import Settings


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


async def main() -> None:
    config = Settings()
    key = config.broker_hmac_key.get_secret_value()
    ca = os.environ.get("SSL_CERT_FILE")
    now = datetime.now(UTC)
    job = {
        "job_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "policy_hash": "a" * 64,
        "approved_origins": ["https://agent.faizanmalik.me"],
        "allowed_paths": ["/qa-fixture/", "/*"],
        "allowed_methods": ["GET", "HEAD"],
        "max_pages": 10,
        "max_redirects": 3,
        "plan": {
            "schema_version": "1.0",
            "goal": "Open fixture and assert heading visible",
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
        },
        "plan_digest": "",
        "limits": {
            "max_duration_seconds": 120,
            "max_actions": 8,
            "memory_bytes": 512 * 1024 * 1024,
            "cpus": 1.0,
            "pids": 128,
            "artifact_bytes": 26_214_400,
        },
        "authorization_expires_at": (now + timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(seconds=90)).isoformat(),
        "nonce": uuid.uuid4().hex + uuid.uuid4().hex,
    }
    job["plan_digest"] = hashlib.sha256(canonical(job["plan"])).hexdigest()
    envelope = {
        "job": job,
        "signature": hmac.new(key.encode(), canonical(job), hashlib.sha256).hexdigest(),
    }
    body = canonical(envelope)
    path = "/internal/v1/sandboxes"
    timestamp = str(int(time.time()))
    signature = hmac.new(
        key.encode(),
        timestamp.encode() + b".POST." + path.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    async with httpx.AsyncClient(verify=ca or True, trust_env=False, timeout=30) as client:
        response = await client.post(
            config.broker_url.rstrip("/") + path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Agentproof-Timestamp": timestamp,
                "X-Agentproof-Signature": signature,
            },
        )
        print("STATUS", response.status_code)
        print("BODY", response.text[:500])


if __name__ == "__main__":
    asyncio.run(main())
