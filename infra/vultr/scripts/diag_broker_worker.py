#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx


def host_env() -> None:
    vals: dict[str, str] = {}
    for line in Path("/etc/agentproof/app.env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            vals[key] = value
    print("has_hmac", "BROKER_HMAC_KEY" in vals)
    print("hmac_len", len(vals.get("BROKER_HMAC_KEY", "")))
    print("broker_url", vals.get("BROKER_URL"))
    print("ssl_cert", vals.get("SSL_CERT_FILE"))
    print("emergency", vals.get("EMERGENCY_STOP_ENABLED"))
    print("app_env", vals.get("APP_ENV"))
    ca = Path(vals.get("SSL_CERT_FILE") or "/etc/agentproof/ca-bundle.crt")
    print("has_ca_file", ca.exists(), ca)


async def worker_probe() -> None:
    print("worker_hmac_len", len(os.environ.get("BROKER_HMAC_KEY", "")))
    print("worker_broker_url", os.environ.get("BROKER_URL"))
    print("worker_ssl", os.environ.get("SSL_CERT_FILE"))
    ca = os.environ.get("SSL_CERT_FILE") or ""
    print("ca_exists", bool(ca) and Path(ca).exists())
    url = (os.environ.get("BROKER_URL") or "").rstrip("/") + "/healthz"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            print("broker_http", response.status_code, response.text[:80])
    except Exception as exc:  # noqa: BLE001
        print("broker_err", type(exc).__name__, str(exc)[:200])


if __name__ == "__main__":
    if os.environ.get("IN_WORKER") == "1":
        asyncio.run(worker_probe())
    else:
        host_env()
