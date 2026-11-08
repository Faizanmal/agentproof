"""Restore a disposable migrated PostgreSQL database and verify its schema revision."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from agentproof_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


async def main() -> int:
    configured = make_url(Settings().database_url.get_secret_value())
    if configured.host not in LOOPBACK or configured.drivername != "postgresql+asyncpg":
        print("BLOCKED: restore test only accepts loopback PostgreSQL.")
        return 2
    pg_dump, pg_restore = shutil.which("pg_dump"), shutil.which("pg_restore")
    if not pg_dump or not pg_restore:
        print("BLOCKED: install matching PostgreSQL client tools (pg_dump and pg_restore).")
        return 2
    suffix = uuid4().hex
    source_name, restored_name = f"agentproof_backup_{suffix}", f"agentproof_restore_{suffix}"
    assert re.fullmatch(r"agentproof_(?:backup|restore)_[a-f0-9]{32}", source_name)
    engine = create_async_engine(configured, isolation_level="AUTOCOMMIT")
    created: list[str] = []
    dump_path = ROOT / ".cache" / f"restore-{suffix}.dump"
    env = dict(os.environ)
    if configured.password:
        env["PGPASSWORD"] = configured.password
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT pg_advisory_lock(941207509)"))
            for name in (source_name, restored_name):
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
                created.append(name)

            source_url = configured.set(database=source_name).render_as_string(hide_password=False)
            env["DATABASE_URL"] = source_url
            migration = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    "apps/api/alembic.ini",
                    "upgrade",
                    "head",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if migration.returncode:
                print("FAIL: disposable source migration failed; see Alembic output locally.")
                return migration.returncode

            base = [
                "--host",
                configured.host or "127.0.0.1",
                "--username",
                configured.username or "postgres",
            ]
            if configured.port:
                base.extend(["--port", str(configured.port)])
            dump = await asyncio.to_thread(
                subprocess.run,
                [
                    pg_dump,
                    *base,
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--dbname",
                    source_name,
                    "--file",
                    str(dump_path),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if dump.returncode:
                print("FAIL: pg_dump failed; database output omitted to avoid credential leakage.")
                return dump.returncode
            restored = await asyncio.to_thread(
                subprocess.run,
                [
                    pg_restore,
                    *base,
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "--dbname",
                    restored_name,
                    str(dump_path),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if restored.returncode:
                print(
                    "FAIL: pg_restore failed; database output omitted to avoid credential leakage."
                )
                return restored.returncode
            target = create_async_engine(configured.set(database=restored_name))
            try:
                async with target.connect() as restored_db:
                    revision = await restored_db.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                    tables = await restored_db.scalar(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema='public'"
                        )
                    )
            finally:
                await target.dispose()
            if not revision or not tables:
                print("FAIL: restored database has no Alembic revision or public schema tables.")
                return 1
            print(f"PASS: disposable PostgreSQL backup restored at Alembic revision {revision}.")
            return 0
    finally:
        dump_path.unlink(missing_ok=True)
        async with engine.connect() as connection:
            for name in reversed(created):
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            await connection.execute(text("SELECT pg_advisory_unlock(941207509)"))
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
