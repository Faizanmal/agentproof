"""Migration acceptance against a disposable database, using Alembic's public API."""

import os
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config


def main() -> None:
    name = urlsplit(os.environ.get("DATABASE_URL", "")).path.removeprefix("/")
    if not name.startswith("agentproof_test_") or len(name) != len("agentproof_test_") + 32:
        raise SystemExit("Migration round-trip requires the disposable integration database")
    config = Config("apps/api/alembic.ini")
    print("Migration: upgrade head", flush=True)
    command.upgrade(config, "head")
    print("Migration: downgrade baseline", flush=True)
    command.downgrade(config, "0001_baseline")
    print("Migration: upgrade head", flush=True)
    command.upgrade(config, "head")
    command.check(config)


if __name__ == "__main__":
    main()
