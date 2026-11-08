"""Local smoke and bounded operator jobs. Never launches browsers."""

import argparse
import asyncio
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agentproof_api.mail import deliver_outbox
from agentproof_api.settings import Settings


def local_smoke(config: Settings) -> None:
    parts = urlsplit(config.api_internal_url)
    if (
        config.app_env != "development"
        or parts.scheme != "http"
        or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise SystemExit("Smoke is restricted to development and loopback HTTP")
    with httpx.Client(
        base_url=config.api_internal_url,
        headers={"Origin": config.public_app_url, "Content-Type": "application/json"},
        timeout=30,
        trust_env=False,
    ) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"smoke-{uuid4().hex}@example.com",
                "password": secrets.token_urlsafe(24),
            },
        )
        assert registered.status_code == 201, f"Registration failed: HTTP {registered.status_code}"
        client.headers["X-CSRF-Token"] = registered.json()["csrf_token"]
        mailbox = client.get("/api/v1/dev/mail")
        assert mailbox.status_code == 200
        verification = urlsplit(mailbox.json()["verification_url"]).fragment.removeprefix("token=")
        assert (
            client.post("/api/v1/auth/verify-email", json={"token": verification}).status_code
            == 200
        )
        workspace = client.post("/api/v1/workspaces", json={"name": "Local smoke workspace"})
        assert workspace.status_code == 201
        assert any(
            w["id"] == workspace.json()["id"]
            for w in client.get("/api/v1/workspaces").json()["data"]
        )
        refreshed = client.post("/api/v1/auth/refresh", json={})
        assert refreshed.status_code == 200
        client.headers["X-CSRF-Token"] = refreshed.json()["csrf_token"]
        assert client.post("/api/v1/auth/logout", json={}).status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 401
    print("PASS: real local account, email verification, workspace persistence, refresh and logout")


async def cleanup_artifacts(config: Settings) -> int:
    engine = create_async_engine(config.database_url.get_secret_value())
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            from agentproof_api.cleanup import process_cleanup

            return await process_cleanup(session, config)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["smoke", "cleanup-artifacts", "deliver-mail", "rotate-credentials"]
    )
    args = parser.parse_args()
    config = Settings()
    if args.command == "smoke":
        local_smoke(config)
    elif args.command == "rotate-credentials":

        async def rotate_job() -> int:
            from agentproof_api.rotate_credentials import rotate_batch

            engine = create_async_engine(config.database_url.get_secret_value())
            try:
                async with async_sessionmaker(engine)() as session, session.begin():
                    return await rotate_batch(session, config)
            finally:
                await engine.dispose()

        print(f"Rotated {asyncio.run(rotate_job())} credential envelopes")
    elif args.command == "deliver-mail":
        print(f"Delivered {asyncio.run(deliver_outbox(config))} verification messages")
    else:
        count = asyncio.run(cleanup_artifacts(config))
        print(f"Completed {count} artifact cleanup jobs")


if __name__ == "__main__":
    main()
