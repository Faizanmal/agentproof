"""Sole provider transport for application agents. Never imported by the runner."""

import asyncio
import json
import random
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentproof_api.settings import Settings


class ProviderError(Exception):
    code = "VULTR_PROVIDER_ERROR"
    status = 502


class NotConfigured(ProviderError):
    code = "LIVE_INFERENCE_BLOCKED"
    status = 503


class AuthenticationError(ProviderError):
    code = "VULTR_AUTHENTICATION_FAILED"
    status = 503


class RateLimitError(ProviderError):
    code = "VULTR_RATE_LIMIT"
    status = 429


class UnavailableError(ProviderError):
    code = "VULTR_UNAVAILABLE"
    status = 503


class ServerError(ProviderError):
    code = "VULTR_SERVER_ERROR"


class ProviderTimeout(ProviderError):
    code = "VULTR_TIMEOUT"
    status = 504


class MalformedResponse(ProviderError):
    code = "VULTR_MALFORMED_RESPONSE"


class RequestRejected(ProviderError):
    code = "VULTR_REQUEST_REJECTED"


class SchemaUnsupported(RequestRejected):
    code = "VULTR_SCHEMA_UNSUPPORTED"


class TruncatedCompletion(MalformedResponse):
    code = "VULTR_COMPLETION_TRUNCATED"


class ModelMismatch(ProviderError):
    code = "VULTR_MODEL_MISMATCH"


class BudgetExhausted(ProviderError):
    code = "MODEL_BUDGET_EXHAUSTED"
    status = 409


@dataclass
class Usage:
    attempt: int
    model_requested: str
    model_actual: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_estimate: Decimal | None = None
    error_code: str | None = None


@dataclass
class Budget:
    max_calls: int = 4
    max_total_tokens: int = 32768
    calls: int = 0
    accounted_tokens: int = 0
    records: list[Usage] = field(default_factory=list)

    def take(self, model: str, completion_limit: int, prompt_bound: int = 0) -> Usage:
        if (
            self.calls >= self.max_calls
            or self.accounted_tokens + completion_limit + prompt_bound > self.max_total_tokens
        ):
            raise BudgetExhausted()
        self.calls += 1
        self.accounted_tokens += completion_limit + prompt_bound
        record = Usage(self.calls, model)
        self.records.append(record)
        return record


class ProviderUsage(BaseModel):
    prompt_tokens: int = Field(ge=0, strict=True)
    completion_tokens: int = Field(ge=0, strict=True)


class Message(BaseModel):
    content: str | None = None
    tool_calls: list[Any] | None = None


class Choice(BaseModel):
    message: Message
    finish_reason: str


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = Field(min_length=1, max_length=160)
    choices: list[Choice] = Field(min_length=1, max_length=1)
    usage: ProviderUsage | None = None


class Modality(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    supported_parameters: dict[str, Any] = Field(default_factory=dict)
    pricing: list[dict[str, Any]] = Field(default_factory=list)


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list)
    input_modalities: list[Modality] = Field(default_factory=list)
    output_modalities: list[Modality] = Field(default_factory=list)
    reasoning: dict[str, Any] = Field(default_factory=dict)


class Catalog(BaseModel):
    data: list[CatalogModel] = Field(min_length=1, max_length=1000)


class VultrGateway:
    def __init__(self, config: Settings, client: httpx.AsyncClient) -> None:
        self.config, self.client = config, client
        self.model: dict[str, Any] | None = None
        self.schema_disabled = False

    def check_configuration(self) -> None:
        if (
            not self.config.vultr_inference_api_key.get_secret_value()
            or not self.config.vultr_inference_model
        ):
            raise NotConfigured()
        # Keys must never be sent to an arbitrary env-supplied provider/proxy.
        if self.config.vultr_inference_base_url.rstrip("/") != "https://api.vultrinference.com/v1":
            raise NotConfigured()

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with self.client.stream(
                method,
                self.config.vultr_inference_base_url.rstrip("/") + path,
                headers={
                    "Authorization": "Bearer "
                    + self.config.vultr_inference_api_key.get_secret_value()
                },
                json=body,
                timeout=self.config.inference_timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code in {401, 403}:
                    raise AuthenticationError()
                if response.status_code == 429:
                    raise RateLimitError()
                if response.status_code in {500, 502, 503, 504}:
                    raise UnavailableError()
                if 500 <= response.status_code <= 599:
                    raise ServerError()
                if response.status_code not in {200, 400}:
                    raise ProviderError()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise MalformedResponse()
                result: Any = json.loads(data)
                if not isinstance(result, dict):
                    raise MalformedResponse()
                if response.status_code == 400:
                    error = result.get("error", {})
                    message = error.get("message", "") if isinstance(error, dict) else ""
                    if isinstance(message, str) and any(
                        word in message.lower()
                        for word in ("schema", "response_format", "structured")
                    ):
                        raise SchemaUnsupported()
                    raise RequestRejected()
                return result
        except httpx.TimeoutException:
            raise ProviderTimeout() from None
        except httpx.TransportError:
            raise UnavailableError() from None
        except (ValueError, UnicodeError):
            raise MalformedResponse() from None

    async def discover(self) -> dict[str, Any]:
        self.check_configuration()
        for attempt in range(3):
            try:
                data = await self._request("GET", "/models")
                break
            except (RateLimitError, UnavailableError, ProviderTimeout):
                if attempt == 2:
                    raise
                await asyncio.sleep(random.uniform(0.05, 0.15) * 2**attempt)
        try:
            entries = [m.model_dump(mode="json") for m in Catalog.model_validate(data).data]
        except ValidationError:
            raise MalformedResponse() from None
        # Aliases are resolved only from provider catalog metadata, never arbitrary defaults.
        matches = [
            m
            for m in entries
            if isinstance(m, dict)
            and (
                m.get("id") == self.config.vultr_inference_model
                or self.config.vultr_inference_model in m.get("aliases", [])
            )
        ]
        if len(matches) != 1 or not any(
            v.get("type") == "text" for v in matches[0].get("output_modalities", [])
        ):
            raise ModelMismatch()
        self.model = matches[0]
        return self.model

    def parameters(self) -> dict[str, Any]:
        assert self.model is not None
        return next(
            v.get("supported_parameters", {})
            for v in self.model["output_modalities"]
            if v.get("type") == "text"
        )

    def formats(self) -> list[str]:
        node: Any = self.parameters().get("response_format", {})
        for key in ["properties", "type", "values"]:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        return [v for v in node if isinstance(v, str)] if isinstance(node, list) else []

    def cost(self, usage: ProviderUsage) -> Decimal | None:
        assert self.model is not None
        total = Decimal(0)
        for group, kind, count in [
            ("input_modalities", "prompt", usage.prompt_tokens),
            ("output_modalities", "completion", usage.completion_tokens),
        ]:
            prices = [
                p
                for v in self.model.get(group, [])
                if v.get("type") == "text"
                for p in v.get("pricing", [])
                if p.get("type") == kind and p.get("unit") == "token" and not p.get("overrides")
            ]
            if len(prices) != 1:
                return None
            try:
                unit = Decimal(prices[0]["cost_usd"])
                if not unit.is_finite() or unit < 0:
                    return None
                total += unit * count
            except (InvalidOperation, KeyError, TypeError):
                return None
        return total

    async def complete(
        self,
        messages: list[dict[str, str]],
        budget: Budget,
        schema: dict[str, Any] | None = None,
        *,
        max_tokens: int | None = None,
    ) -> str:
        self.check_configuration()
        limit = min(
            max_tokens or self.config.inference_max_completion_tokens,
            self.config.inference_max_completion_tokens,
        )
        body: dict[str, Any] = {
            "model": self.config.vultr_inference_model,
            "messages": messages,
            "max_completion_tokens": limit,
            "stream": False,
        }
        # UTF-8 bytes plus framing are a conservative tokenizer-independent upper bound.
        prompt_bound = (
            len(
                json.dumps(
                    {"messages": messages, "schema": schema},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            )
            + 512
        )
        if (
            prompt_bound > 64000
            or budget.accounted_tokens + prompt_bound + limit > budget.max_total_tokens
        ):
            raise BudgetExhausted()
        if self.model is None:
            await self.discover()
        formats = self.formats()
        if schema is not None and "json_schema" in formats and not self.schema_disabled:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "QAPlanV1", "strict": False, "schema": schema},
            }
        elif schema is not None and "json_object" in formats:
            body["response_format"] = {"type": "json_object"}
        if self.model and self.model.get("reasoning", {}).get("supports_max_tokens") is True:
            body["reasoning"] = {"max_tokens": min(512, limit // 4)}
        temperature = self.parameters().get("temperature")
        if isinstance(temperature, dict) and temperature.get("min", 0) == 0:
            body["temperature"] = 0
        # This gateway sends text only. Optional controls are used only when advertised.
        for retry in range(3):
            record = budget.take(self.config.vultr_inference_model, limit, prompt_bound)
            try:
                raw = await self._request("POST", "/chat/completions", body)
                envelope = Envelope.model_validate(raw)
                record.model_actual = envelope.model
                if envelope.usage is not None:
                    record.prompt_tokens = envelope.usage.prompt_tokens
                    record.completion_tokens = envelope.usage.completion_tokens
                    record.cost_estimate = self.cost(envelope.usage)
                    budget.accounted_tokens += (
                        record.prompt_tokens + record.completion_tokens - limit - prompt_bound
                    )
                assert self.model is not None
                if envelope.model != self.model["id"]:
                    raise ModelMismatch()
                if budget.accounted_tokens > budget.max_total_tokens:
                    raise BudgetExhausted()
                choice = envelope.choices[0]
                if choice.finish_reason == "length":
                    raise TruncatedCompletion()
                if (
                    choice.message.tool_calls
                    or choice.finish_reason != "stop"
                    or not choice.message.content
                ):
                    raise MalformedResponse()
                return choice.message.content
            except SchemaUnsupported as error:
                record.error_code = error.code
                self.schema_disabled = True
                if body.get("response_format", {}).get("type") != "json_schema" or retry == 2:
                    raise
                if "json_object" in formats:
                    body["response_format"] = {"type": "json_object"}
                else:
                    body.pop("response_format", None)
                # Capability downgrade only for explicit schema rejection, not generic 400 retries.
                continue
            except ValidationError:
                record.error_code = MalformedResponse.code
                raise MalformedResponse() from None
            except ProviderError as error:
                record.error_code = error.code
                if (
                    not isinstance(error, (RateLimitError, UnavailableError, ProviderTimeout))
                    or retry == 2
                ):
                    raise
                await asyncio.sleep(random.uniform(0.05, 0.15) * 2**retry)
        raise UnavailableError()
