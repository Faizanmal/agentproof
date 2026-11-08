"""Create a disposable owned Postgres database; migrate, test, and drop in finally."""

import argparse
import asyncio
import os
import re
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


async def main(*, e2e: bool = False) -> int:
    # Use the configured local role. Refuse any non-loopback target.
    configured = make_url(Settings().database_url.get_secret_value())
    if configured.host not in LOOPBACK or configured.drivername != "postgresql+asyncpg":
        raise SystemExit("Integration refuses every database that is not loopback PostgreSQL")
    engine = create_async_engine(configured, isolation_level="AUTOCOMMIT")
    connection = await engine.connect()
    await connection.execute(text("SELECT pg_advisory_lock(723686195)"))
    name = "agentproof_test_" + uuid4().hex
    if re.fullmatch(r"agentproof_test_[0-9a-f]{32}", name) is None:
        raise SystemExit("Refusing an unexpected disposable database name")
    print("Disposable database: " + name, flush=True)
    created = False
    env = dict(os.environ)
    disposable = configured.set(database=name)
    env["DATABASE_URL"] = disposable.render_as_string(hide_password=False)
    env["TEST_DATABASE_URL"] = env["DATABASE_URL"]
    env["UV_CACHE_DIR"] = str(ROOT / ".cache/uv")
    pytest_base = ROOT / ".cache" / f"pytest-integration-{uuid4().hex}"
    commands = [
        [sys.executable, "scripts/migration_check.py"],
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration",
            "-q",
            "-s",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(pytest_base),
            "--tb=short",
        ],
    ]
    if e2e:
        commands.append(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e",
                "-q",
                "-s",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(pytest_base / "browser"),
                "--tb=short",
            ]
        )
    try:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
        created = True
        for command in commands:
            print("+ " + " ".join(command), flush=True)
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                env=env,
                cwd=ROOT,
                timeout=600,
                check=False,
            )
            if result.returncode:
                return result.returncode
        return 0
    finally:
        if created:
            # Do not terminate arbitrary backend sessions from the configured
            # development role. Tests must close their own pools before cleanup.
            await connection.execute(text(f'DROP DATABASE "{name}"'))
        await connection.execute(text("SELECT pg_advisory_unlock(723686195)"))
        await connection.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--e2e", action="store_true", help="Include Chromium browser execution tests"
    )
    raise SystemExit(asyncio.run(main(e2e=parser.parse_args().e2e)))
