import asyncio
from typing import cast

from agentproof_api.dependencies import ServiceProbe
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine


class BrokenEngine:
    def connect(self) -> None:
        raise OSError("private-database-sentinel")


class SlowRedis:
    async def ping(self) -> bool:
        await asyncio.sleep(60)
        return True


async def test_real_probe_contains_connection_errors_and_timeouts() -> None:
    probe = ServiceProbe(cast(AsyncEngine, BrokenEngine()), cast(Redis, SlowRedis()), 0.01)
    async with asyncio.timeout(1):
        result = await probe.check()
    assert result.postgres == "down" and result.redis == "down"
