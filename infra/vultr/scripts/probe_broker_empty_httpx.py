#!/usr/bin/env python3
import asyncio
import hashlib
import hmac
import os
import time

import httpx
from agentproof_api.settings import Settings


async def main() -> None:
    config = Settings()
    key = config.broker_hmac_key.get_secret_value()
    ca = os.environ.get("SSL_CERT_FILE")
    body = b"{}"
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
        print("BODY", response.text[:300])
        print("KEY_FP", hashlib.sha256(key.encode()).hexdigest()[:16])
        print("BODY_LEN", len(body))


if __name__ == "__main__":
    asyncio.run(main())
