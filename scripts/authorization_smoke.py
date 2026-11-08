"""Real loopback HTTP authorization smoke. No browser or public target."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from agentproof_api.policy import Action, TargetPolicy, check_action
from agentproof_api.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
TARGET = "http://127.0.0.1:19090"


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"AgentProof operator-owned demo fixture")

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-plan", action="store_true")
    args = parser.parse_args()
    config = Settings()
    if config.app_env != "development" or urlsplit(
        config.database_url.get_secret_value()
    ).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("Smoke requires development and a loopback database")
    fixture = ThreadingHTTPServer(("127.0.0.1", 19090), Fixture)
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, DEMO_MODE_ENABLED="true", DEMO_ORIGINS=json.dumps([TARGET]))
    with (ROOT / ".cache/authorization-smoke-api.log").open("w") as log:
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agentproof_api.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "18003",
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url="http://127.0.0.1:18003",
                headers={"Origin": config.public_app_url, "Content-Type": "application/json"},
                trust_env=False,
                timeout=10,
            ) as client:
                for _ in range(60):
                    if api.poll() is not None:
                        raise RuntimeError("Smoke API exited; inspect local ignored log")
                    try:
                        if client.get("/api/v1/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError("Local dependencies not ready")
                identity = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"authorization-smoke-{uuid4().hex}@example.com",
                        "password": secrets.token_urlsafe(32),
                    },
                )
                assert identity.status_code == 201
                client.headers["X-CSRF-Token"] = identity.json()["csrf_token"]
                mail = client.get("/api/v1/dev/mail").json()
                token = urlsplit(mail["verification_url"]).fragment.removeprefix("token=")
                assert (
                    client.post("/api/v1/auth/verify-email", json={"token": token}).status_code
                    == 200
                )
                wid = client.post(
                    "/api/v1/workspaces", json={"name": "Authorization smoke"}
                ).json()["id"]
                result = client.post(
                    "/api/v1/projects",
                    json={"workspace_id": wid, "name": "Operator-owned demo", "base_url": TARGET},
                )
                assert result.status_code == 201
                pid = result.json()["id"]
                endpoint = f"/api/v1/projects/{pid}"
                issued = client.post(
                    endpoint + "/verification-challenges", json={"origin": TARGET, "method": "demo"}
                )
                assert issued.status_code == 201
                verified = client.post(
                    endpoint + "/verify-target",
                    json={"origin_id": issued.json()["id"], "attestation": True},
                )
                assert verified.status_code == 200
                grants = client.get(endpoint + "/authorized-origins")
                assert len(grants.json()["data"]) == 1
                policy = TargetPolicy(authorized_origins=[TARGET])
                assert (
                    client.patch(
                        endpoint + "/target-policy", json=policy.model_dump(mode="json")
                    ).status_code
                    == 200
                )
                local = config.model_copy(
                    update={"demo_mode_enabled": True, "demo_origins": [TARGET]}
                )
                check_action(policy, Action(url=TARGET), local)
                assert client.get(TARGET).text == "AgentProof operator-owned demo fixture"
                if args.generate_plan:
                    goal = (
                        "Navigate to / and assert the visible text "
                        "AgentProof operator-owned demo fixture. "
                        "Use a text locator and assert_visible. Do not click, fill or interact."
                    )
                    planned = client.post(
                        endpoint + "/plans",
                        json={
                            "goal": goal,
                            "scope": {"start_path": "/", "allowed_paths": ["/"]},
                            "untrusted_page_text": client.get(TARGET).text,
                        },
                        timeout=95,
                    )
                    if planned.status_code != 201:
                        code = planned.json().get("code", "UNKNOWN")
                        client.delete(endpoint)
                        raise AssertionError(
                            "Live planner failed: " + str(planned.status_code) + " " + code
                        )
                    draft = planned.json()
                    assert (
                        draft["status"] == "draft" and draft["plan_json"]["schema_version"] == "1.0"
                    )
                    approved = client.post(
                        f"/api/v1/plans/{draft['id']}/approve",
                        json={
                            "version": draft["version"],
                            "authorization_digest": draft["authorization_digest"],
                            "approved_step_ids": [
                                s["id"]
                                for s in draft["plan_json"]["steps"]
                                if s["requires_approval"]
                            ],
                            "approve_high_risk": True,
                        },
                    )
                    assert approved.status_code == 200, "Live approval failed: " + str(
                        approved.status_code
                    )
                    event = client.get(f"/api/v1/plans/{draft['id']}/events")
                    assert any(e["event_type"] == "plan.generated" for e in event.json()["data"])
                    print("PASS: live Vultr QAPlanV1 persisted, validated and explicitly approved")
                assert client.delete(endpoint).status_code == 204
                print(
                    "PASS: real HTTP owner login, attestation, grant, policy and owned fixture; "
                    "project removed"
                )
        finally:
            api.terminate()
            try:
                api.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait()
            fixture.shutdown()
            fixture.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
