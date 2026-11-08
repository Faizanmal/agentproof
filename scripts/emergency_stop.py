"""Authenticated operator command to kill every active broker sandbox lease."""

from __future__ import annotations

import hashlib
import hmac

import httpx
from agentproof_api.settings import Settings


def main() -> int:
    config = Settings()
    key = config.broker_hmac_key.get_secret_value()
    if len(key) < 32:
        print("BLOCKED: BROKER_HMAC_KEY must be configured in the operator environment.")
        return 2
    path = "/internal/v1/emergency-stop"
    timestamp = str(int(__import__("time").time()))
    signed = timestamp.encode() + b".POST." + path.encode() + b"."
    signature = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
    try:
        with httpx.Client(trust_env=False, timeout=20) as client:
            response = client.post(
                config.broker_url.rstrip("/") + path,
                content=b"",
                headers={
                    "X-Agentproof-Timestamp": timestamp,
                    "X-Agentproof-Signature": signature,
                },
            )
    except httpx.HTTPError:
        print(
            "FAILED: broker emergency-stop endpoint is unreachable; inspect "
            "broker inventory directly."
        )
        return 1
    if response.status_code != 200:
        print(
            f"FAILED: broker returned HTTP {response.status_code}; no cleanup success is claimed."
        )
        return 1
    result = response.json()
    print(
        f"Emergency stop requested={result.get('requested')} cleaned={result.get('cleaned')}. "
        "Inspect outcomes and verify the broker inventory is empty."
    )
    return 0 if result.get("requested") == result.get("cleaned") else 1


if __name__ == "__main__":
    raise SystemExit(main())
