#!/usr/bin/env python3
"""Operator E2E: login → workspace → verify origin → plan → approve → run."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


BASE = os.environ.get("PUBLIC_APP_URL", "https://agent.faizanmalik.me").rstrip("/")
EMAIL = os.environ["E2E_EMAIL"]
PASSWORD = os.environ["E2E_PASSWORD"]
ORIGIN = os.environ.get("E2E_ORIGIN", BASE)
WELLKNOWN = Path(os.environ.get("WELLKNOWN_FILE", "/var/lib/agentproof/well-known/agentproof-verification"))


class Client:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.csrf = ""
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | str]:
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "Origin": BASE, "Accept": "application/json"}
        if self.csrf and method.upper() not in {"GET", "HEAD"}:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=120) as resp:
                raw = resp.read().decode()
                parsed: dict | list | str = json.loads(raw) if raw else {}
                return resp.status, parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                parsed = json.loads(raw) if raw else {"detail": raw}
            except json.JSONDecodeError:
                parsed = {"detail": raw}
            return exc.code, parsed


def main() -> int:
    client = Client()
    code, identity = client.call("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
    if code != 200:
        print("LOGIN_FAIL", code, identity)
        return 1
    assert isinstance(identity, dict)
    client.csrf = str(identity["csrf_token"])
    print("LOGIN_OK", identity.get("user", {}).get("email"))

    code, workspaces = client.call("GET", "/api/v1/workspaces")
    assert isinstance(workspaces, dict)
    items = workspaces.get("data") or []
    if not items:
        code, ws = client.call("POST", "/api/v1/workspaces", {"name": "Challenge1 Workspace"})
        if code != 201:
            print("WORKSPACE_FAIL", code, ws)
            return 1
        assert isinstance(ws, dict)
        workspace_id = ws["id"]
    else:
        workspace_id = items[0]["id"]
    print("WORKSPACE", workspace_id)

    code, projects = client.call("GET", f"/api/v1/workspaces/{workspace_id}/projects")
    if code != 200:
        code, projects = client.call("GET", f"/api/v1/projects?workspace_id={workspace_id}")
    assert isinstance(projects, dict)
    projects_data = projects.get("data") or []
    project = next((p for p in projects_data if str(p.get("base_url", "")).rstrip("/") == ORIGIN), None)
    if project is None:
        code, project = client.call(
            "POST",
            "/api/v1/projects",
            {"workspace_id": workspace_id, "name": "Fixture Project", "base_url": ORIGIN},
        )
        if code != 201:
            print("PROJECT_FAIL", code, project)
            return 1
    assert isinstance(project, dict)
    project_id = project["id"]
    print("PROJECT", project_id)

    code, challenge = client.call(
        "POST",
        f"/api/v1/projects/{project_id}/verification-challenges",
        {"origin": ORIGIN, "method": "https-file"},
    )
    if code not in {200, 201}:
        print("CHALLENGE_FAIL", code, challenge)
        return 1
    assert isinstance(challenge, dict)
    token = challenge.get("challenge") or challenge.get("token")
    if not token:
        print("CHALLENGE_TOKEN_MISSING", challenge)
        return 1
    WELLKNOWN.parent.mkdir(parents=True, exist_ok=True)
    WELLKNOWN.write_text(str(token).strip() + "\n", encoding="utf-8")
    print("WELLKNOWN_WRITTEN", WELLKNOWN)

    code, verified = client.call(
        "POST",
        f"/api/v1/projects/{project_id}/verify-target",
        {"origin_id": challenge["id"]},
    )
    if code != 200:
        print("VERIFY_FAIL", code, verified)
        return 1
    print("ORIGIN_VERIFIED")

    goal = "Open the owned fixture home page and confirm the heading AgentProof owned fixture is visible."
    code, plan = client.call(
        "POST",
        f"/api/v1/projects/{project_id}/plans",
        {
            "goal": goal,
            "scope": {
                "start_path": "/qa-fixture/",
                "allowed_paths": ["/", "/qa-fixture", "/qa-fixture/", "/qa-fixture/index.html", "/*"],
            },
        },
    )
    if code != 201:
        print("PLAN_FAIL", code, plan)
        return 1
    assert isinstance(plan, dict)
    plan_id = plan["id"]
    print("PLAN_CREATED", plan_id, plan.get("status"), plan.get("risk_level"))
    plan_json = plan.get("plan_json") or {}
    step_ids = [s["id"] for s in (plan_json.get("steps") or [])]
    code, approved = client.call(
        "POST",
        f"/api/v1/plans/{plan_id}/approve",
        {
            "version": plan["version"],
            "authorization_digest": plan["authorization_digest"],
            "approved_step_ids": step_ids,
            "approve_high_risk": True,
        },
    )
    if code != 200:
        print("APPROVE_FAIL", code, approved)
        return 1
    print("PLAN_APPROVED")

    code, run = client.call(
        "POST",
        f"/api/v1/projects/{project_id}/runs",
        {"plan_id": plan_id, "idempotency_key": f"challenge1-{int(time.time())}"},
    )
    if code not in {200, 201, 202}:
        print("RUN_FAIL", code, run)
        return 1
    assert isinstance(run, dict)
    run_id = run.get("id") or run.get("run_id")
    print("RUN_CREATED", run_id, run.get("status"))

    terminal = {"completed", "failed", "cancelled"}
    for _ in range(120):
        code, status = client.call("GET", f"/api/v1/runs/{run_id}")
        if code != 200 or not isinstance(status, dict):
            print("RUN_POLL_FAIL", code, status)
            time.sleep(2)
            continue
        state = status.get("status")
        print("RUN_STATUS", state, status.get("error_code"))
        if state in terminal:
            print("RUN_DONE", json.dumps(status)[:800])
            return 0 if state == "completed" else 2
        time.sleep(2)
    print("RUN_TIMEOUT")
    return 3


if __name__ == "__main__":
    sys.exit(main())
