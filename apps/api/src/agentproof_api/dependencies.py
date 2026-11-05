import asyncio
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from agentproof_api.schemas import Dependencies


class DependencyProbe(Protocol):
    async def check(self) -> Dependencies: ...


class ServiceProbe:
    def __init__(self, engine: AsyncEngine, redis: Redis, timeout: float) -> None:
        self.engine = engine
        self.redis = redis
        self.timeout = timeout

    async def postgres_available(self) -> bool:
        try:
            async with asyncio.timeout(self.timeout), self.engine.connect() as connection:
                result = await connection.execute(text("SELECT 1"))
                return result.scalar_one() == 1
        except Exception:
            return False

    async def redis_available(self) -> bool:
        try:
            async with asyncio.timeout(self.timeout):
                return bool(await self.redis.ping())
        except Exception:
            return False

    async def check(self) -> Dependencies:
        postgres, redis = await asyncio.gather(self.postgres_available(), self.redis_available())
        return Dependencies(postgres="up" if postgres else "down", redis="up" if redis else "down")
