import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ci_yaml_has_safe_permissions_and_checks() -> None:
    workflow: dict[str, Any] = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in workflow
    jobs = workflow["jobs"]
    assert {
        "lint",
        "typecheck",
        "unit",
        "integration-postgres-redis",
        "frontend-e2e",
        "build-images",
        "security-audit",
    } <= set(jobs)
    commands = "\n".join(
        step.get("run", "")
        for job in jobs.values()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )
    for task in ("lint", "typecheck", "integration", "security-audit"):
        assert f"manage.py {task}" in commands
    assert "pytest tests/unit" in commands
    assert "docker build" in commands


def test_compose_exposes_dependencies_only_on_loopback() -> None:
    compose = yaml.safe_load((ROOT / "infra/compose.dev.yml").read_text())
    for name in ("postgres", "redis"):
        service = compose["services"][name]
        assert all(port.startswith("127.0.0.1:") for port in service["ports"])
        assert "healthcheck" in service
        assert service["volumes"]


def test_application_compose_has_no_runtime_socket_or_privileged_mode() -> None:
    for path in ("infra/compose.dev.yml", "infra/compose.prod.yml"):
        compose = yaml.safe_load((ROOT / path).read_text())
        for service in compose["services"].values():
            assert not service.get("privileged", False)
            assert service.get("network_mode") != "host"
            for volume in service.get("volumes", []):
                assert "sock" not in str(volume).lower()
                if path.endswith("compose.dev.yml"):
                    assert not str(volume).startswith(("/", ".", "~"))
        assert "runner" not in compose["services"]
    prod = yaml.safe_load((ROOT / "infra/compose.prod.yml").read_text())
    caddy = prod["services"]["caddy"]
    assert caddy["depends_on"] == {
        "api": {"condition": "service_healthy"},
        "web": {"condition": "service_healthy"},
    }
    assert caddy["healthcheck"]["test"] == [
        "CMD",
        "wget",
        "-q",
        "-O",
        "/dev/null",
        "http://127.0.0.1:2019/config/",
    ]


@pytest.mark.parametrize("component", ["worker", "runner"])
def test_execution_scaffolds_fail_closed(component: str) -> None:
    command = [sys.executable, "-m", f"agentproof_{component}.main"]
    checked = subprocess.run([*command, "--check"], capture_output=True, text=True, timeout=10)
    assert checked.returncode == 0
    assert '"execution_enabled": false' in checked.stdout
    if component == "runner":
        refused = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert refused.returncode == 2
    else:
        configured = subprocess.run(
            [
                sys.executable,
                "-c",
                "from agentproof_worker.main import WorkerSettings; "
                "assert WorkerSettings.job_timeout >= 750 and WorkerSettings.functions",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert configured.returncode == 0, configured.stderr


def test_env_template_contains_all_required_names() -> None:
    template = (ROOT / ".env.example").read_text()
    for name in (
        "DATABASE_URL",
        "REDIS_URL",
        "PUBLIC_APP_URL",
        "API_INTERNAL_URL",
        "VULTR_INFERENCE_BASE_URL",
        "VULTR_INFERENCE_MODEL",
        "VULTR_INFERENCE_API_KEY",
        "MASTER_ENCRYPTION_KEY",
        "SESSION_SIGNING_KEY",
        "ARTIFACT_STORAGE_ROOT",
        "BROKER_URL",
        "BROKER_HMAC_KEY",
    ):
        assert f"{name}=" in template
