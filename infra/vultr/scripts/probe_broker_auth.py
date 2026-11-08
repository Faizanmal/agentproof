#!/usr/bin/env python3
"""Signed auth probe against the private broker using /etc/agentproof/app.env."""

from __future__ import annotations

import hmac
import hashlib
import ssl
import urllib.error
import urllib.request
from pathlib import Path


def load_env(path: str) -> dict[str, str]:
    vals: dict[str, str] = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        vals[key] = value
    return vals


def main() -> None:
    import time

    env = load_env("/etc/agentproof/app.env")
    key = env["BROKER_HMAC_KEY"]
    base = env["BROKER_URL"].rstrip("/")
    ca = env.get("SSL_CERT_FILE")
    body = b"{}"
    path = "/internal/v1/sandboxes"
    method = "POST"
    timestamp = str(int(time.time()))
    signed = timestamp.encode() + b"." + method.encode() + b"." + path.encode() + b"." + body
    signature = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        base + path,
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Agentproof-Timestamp": timestamp,
            "X-Agentproof-Signature": signature,
        },
    )
    context = ssl.create_default_context(cafile=ca) if ca else None
    try:
        with urllib.request.urlopen(req, context=context, timeout=15) as resp:
            print("STATUS", resp.status)
            print("BODY", resp.read()[:200])
    except urllib.error.HTTPError as exc:
        print("STATUS", exc.code)
        print("BODY", exc.read()[:300])


if __name__ == "__main__":
    main()
