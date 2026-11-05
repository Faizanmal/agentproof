import asyncio

from agentproof_api.settings import Settings
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine


def offline() -> None:
    context.configure(
        url=Settings().database_url.get_secret_value(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    engine = create_async_engine(
        Settings().database_url.get_secret_value(), poolclass=pool.NullPool
    )
    try:
        async with engine.connect() as connection:

            def migrate(sync_connection: Connection) -> None:
                context.configure(connection=sync_connection)
                with context.begin_transaction():
                    context.run_migrations()

            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    offline()
else:
    asyncio.run(online())
