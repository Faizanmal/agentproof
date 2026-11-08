"""Inspect public capabilities for the configured model; never prints credentials."""

import asyncio
import json

import httpx
from agentproof_api.settings import Settings


async def main() -> None:
    s = Settings()
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        response = await client.get(s.vultr_inference_base_url + "/models")
        response.raise_for_status()
        models = response.json()["data"]
        selected = [m for m in models if m.get("id") == s.vultr_inference_model]
        print(
            json.dumps(
                {
                    "configured_model": s.vultr_inference_model,
                    "catalog_count": len(models),
                    "selected": selected,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
