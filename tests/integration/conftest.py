import asyncio
import base64
import copy
import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from agentproof_api.app import create_app
from agentproof_api.auth import database
from agentproof_api.model_gateway import VultrGateway
from agentproof_api.planner import gateway
from agentproof_api.settings import Settings
from arq import create_pool
from arq.connections import RedisSettings
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def environment(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not urlsplit(url).path.removeprefix("/").startswith("agentproof_test_"):
        pytest.skip("Run python scripts/manage.py integration for a disposable database")
    config = Settings(
        _env_file=None,
        app_env="development",
        vultr_inference_model="fixture-model",
        vultr_inference_api_key=SecretStr(secrets.token_urlsafe(32)),
        database_url=SecretStr(url),
        redis_url=SecretStr("redis://127.0.0.1:6379/15"),
        session_signing_key=SecretStr(secrets.token_urlsafe(40)),
        master_encryption_key=SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
        artifact_storage_root=tmp_path,
        auth_rate_limit=3,
        demo_mode_enabled=True,
        demo_origins=["http://127.0.0.1:19090"],
    )
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        app = create_app(config)
        async with app.router.lifespan_context(app):
            app.state.session_factory = factory
            plain_redis = app.state.redis
            redis = await create_pool(
                RedisSettings.from_dsn(config.redis_url.get_secret_value())
            )
            app.state.redis = redis
            # Isolated Redis DB 15, namespaced keys cleared only for this dedicated test DB.
            await redis.flushdb()

            async def override() -> AsyncIterator[AsyncSession]:
                async with factory() as session:
                    try:
                        yield session
                        await session.commit()
                    except BaseException:
                        await session.rollback()
                        raise

            fixture_root = Path(__file__).parents[1] / "fixtures/vultr"
            catalog = json.loads(await asyncio.to_thread((fixture_root / "models.json").read_text))
            completion = json.loads(
                await asyncio.to_thread((fixture_root / "completion.json").read_text)
            )
            provider_responses: list[httpx.Response] = []

            def transport(request: httpx.Request) -> httpx.Response:
                if request.method == "GET":
                    return httpx.Response(200, json=catalog)
                if provider_responses:
                    return provider_responses.pop(0)
                body = json.loads(request.content)
                quoted = json.loads(body["messages"][1]["content"])
                payload = copy.deepcopy(completion)
                plan = json.loads(payload["choices"][0]["message"]["content"])
                plan["goal"] = quoted["goal"]
                plan["steps"][0]["url_path"] = quoted["test_scope"]["start_path"]
                payload["choices"][0]["message"]["content"] = json.dumps(plan)
                return httpx.Response(200, json=payload)

            async def provider_override() -> AsyncIterator[VultrGateway]:
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(transport)
                ) as provider_client:
                    yield VultrGateway(config, provider_client)

            app.dependency_overrides[gateway] = provider_override
            app.dependency_overrides[database] = override
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "Content-Type": "application/json"},
            ) as client:
                yield {
                    "app": app,
                    "client": client,
                    "factory": factory,
                    "config": config,
                    "engine": engine,
                    "root": tmp_path,
                    "provider_responses": provider_responses,
                }
            await redis.flushdb()
            await redis.aclose()
            app.state.redis = plain_redis
        await transaction.rollback()
    await engine.dispose()
