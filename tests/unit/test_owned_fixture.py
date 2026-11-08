"""Fixture state tests: the displayed count is persisted, not browser-mocked."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def owned_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ModuleType, TestClient, dict[str, str]]]:
    state = tmp_path / "fixture"
    monkeypatch.setenv("FIXTURE_STATE_ROOT", str(state))
    monkeypatch.setenv("FIXTURE_DATABASE_PATH", str(state / "db.sqlite3"))
    monkeypatch.setenv("FIXTURE_CREDENTIALS_FILE", str(state / "credentials.json"))
    monkeypatch.setenv("FIXTURE_SEED_EMAIL", "qa@example.test")
    monkeypatch.setenv("BUG_MODE", "on")
    module_path = Path(__file__).parents[1] / "fixtures" / "site" / "server.py"
    spec = importlib.util.spec_from_file_location("agentproof_owned_fixture", module_path)
    assert spec is not None and spec.loader is not None
    fixture_server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fixture_server
    spec.loader.exec_module(fixture_server)
    with TestClient(fixture_server.app, follow_redirects=False) as client:
        credentials = json.loads((state / "credentials.json").read_text(encoding="utf-8"))
        yield fixture_server, client, credentials


def login(client: TestClient, credentials: dict[str, str]) -> None:
    response = client.post(
        "/demo/login", data={"email": credentials["email"], "password": credentials["password"]}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/demo/dashboard"


@pytest.mark.parametrize(("mode", "expected_count"), [("on", "3"), ("off", "2")])
def test_subscription_bug_is_real_persisted_state(
    owned_fixture: tuple[ModuleType, TestClient, dict[str, str]], mode: str, expected_count: str
) -> None:
    fixture_server, client, credentials = owned_fixture
    admin = {"X-Fixture-Admin": credentials["admin_token"]}
    response = client.post(
        "/__agentproof_fixture__/admin/mode", params={"mode": mode}, headers=admin
    )
    assert response.status_code == 200
    login(client, credentials)
    run_id = "12345678-1234-4234-8234-123456789abc"
    label = f"AgentProof-E2E-{run_id}-monthly-plan"
    assert client.get("/demo/dashboard").status_code == 200
    assert (
        client.post("/demo/subscriptions", data={"label": label, "interval": "monthly"}).status_code
        == 303
    )
    html = client.get("/demo/subscriptions").text
    assert label in html and "monthly" in html
    subscription = re.search(r'data-subscription-id="([a-f0-9]+)"', html)
    assert subscription
    assert (
        client.post(
            "/demo/subscriptions/" + subscription.group(1) + "/interval",
            data={"interval": "yearly"},
        ).status_code
        == 303
    )
    html = client.get("/demo/subscriptions").text
    assert "yearly" in html
    subscription = re.search(r'data-subscription-id="([a-f0-9]+)"', html)
    assert subscription
    subscription_id = subscription.group(1)
    assert client.post(f"/demo/subscriptions/{subscription_id}/cancel").status_code == 303
    html = client.get("/demo/subscriptions").text
    assert "Cancelled" in html
    count = re.search(
        r'data-testid="active-count-label">Active subscriptions: (\d+)',
        client.get("/demo/dashboard").text,
    )
    assert count and count.group(1) == expected_count
    if mode == "off":
        with fixture_server._connect() as connection:
            actual = connection.execute(
                "SELECT count(*) FROM subscriptions WHERE status='active'"
            ).fetchone()[0]
        assert int(count.group(1)) == actual == 2
    reset = client.post(
        "/__agentproof_fixture__/admin/reset", params={"run_id": run_id}, headers=admin
    )
    assert reset.status_code == 200
    assert reset.json()["synthetic_rows_deleted"] == 1


def test_registration_verifies_through_private_fake_mailbox(
    owned_fixture: tuple[ModuleType, TestClient, dict[str, str]],
) -> None:
    _fixture_server, client, _credentials = owned_fixture
    response = client.post(
        "/demo/register",
        data={
            "email": "synthetic.user@example.test",
            "password": "temporary synthetic password",  # pragma: allowlist secret
        },
    )
    assert response.status_code == 303
    mailbox = client.get("/demo/mailbox")
    assert "no email or SMS was sent" in mailbox.text
    token = re.search(r"/demo/verify\?token=([a-f0-9]+)", mailbox.text)
    assert token
    verify = client.get("/demo/verify", params={"token": token.group(1)})
    assert verify.status_code == 303
    assert verify.headers["location"] == "/demo/login?verified=1"


def test_fixture_admin_is_private_and_reset_requires_run_scope(
    owned_fixture: tuple[ModuleType, TestClient, dict[str, str]],
) -> None:
    _fixture_server, client, credentials = owned_fixture
    assert (
        client.post("/__agentproof_fixture__/admin/mode", params={"mode": "off"}).status_code == 404
    )
    denied = client.post(
        "/__agentproof_fixture__/admin/reset",
        params={"run_id": "all"},
        headers={"X-Fixture-Admin": credentials["admin_token"]},
    )
    assert denied.status_code == 422
