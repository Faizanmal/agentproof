"""Real configured Vultr smoke. Sanitized metadata only; no generated content."""

import asyncio
import json
from pathlib import Path

import httpx
from agentproof_api.model_gateway import Budget, ProviderError, VultrGateway
from agentproof_api.settings import Settings


async def main() -> int:
    config = Settings()
    evidence: dict[str, object] = {"live": True, "provider": "Vultr"}
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            gateway = VultrGateway(config, client)
            model = await gateway.discover()
            budget = Budget(max_calls=2)
            output = await gateway.complete(
                [{"role": "user", "content": "Reply only OK"}], budget, max_tokens=64
            )
            if output.strip() != "OK":
                evidence["status"] = "LIVE_INFERENCE_FAILED_UNEXPECTED_REPLY"
                print(json.dumps(evidence))
                return 1
            evidence.update(
                status="PASS",
                model_requested=config.vultr_inference_model,
                model_actual=model["id"],
                calls=budget.calls,
                usage=[
                    {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens}
                    for u in budget.records
                ],
                features={
                    "structured_schema": "json_schema"
                    in gateway.parameters()
                    .get("response_format", {})
                    .get("properties", {})
                    .get("type", {})
                    .get("values", []),
                    "image_input_advertised": any(
                        m.get("type") == "image" for m in model.get("input_modalities", [])
                    ),
                },
            )
    except ProviderError as error:
        evidence.update(
            status=error.code, required_env=["VULTR_INFERENCE_API_KEY", "VULTR_INFERENCE_MODEL"]
        )
        print(json.dumps(evidence))
        return 2
    target = Path("internal-docs/evidence/phase04/live-inference.json")
    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text, json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
