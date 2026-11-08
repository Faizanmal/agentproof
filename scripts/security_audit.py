"""Run dependency and Git-history scanners; missing tools are a blocking result."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str], *, quiet_stdout: bool = False) -> None:
    print(f"+ {label}", flush=True)
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        timeout=900,
        stdout=subprocess.DEVNULL if quiet_stdout else None,
    )
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> int:
    required = ("uv", "pnpm", "docker")
    missing = [tool for tool in required if shutil.which(tool) is None]
    if missing:
        print("BLOCKED: security audit tools are missing: " + ", ".join(missing))
        return 2
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    requirements = cache / "security-audit-requirements.txt"
    run(
        "export locked Python runtime requirements",
        [
            "uv",
            "export",
            "--locked",
            "--all-packages",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements),
        ],
        quiet_stdout=True,
    )
    run(
        "pip-audit locked Python requirements",
        ["uvx", "--from", "pip-audit==2.9.0", "pip-audit", "--requirement", str(requirements)],
    )
    run("pnpm audit high and critical", ["pnpm", "audit", "--audit-level", "high"])
    run("detect-secrets candidate files", [sys.executable, "scripts/security_check.py"])
    run(
        "Gitleaks working tree and full Git history",
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{ROOT}:/repo:ro",
            "zricethezav/gitleaks:v8.24.2",
            "detect",
            "--source",
            "/repo",
            "--log-opts=--all",
            "--redact",
            "--no-banner",
        ],
    )
    run(
        "Trivy dependency, secret, and configuration scan",
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{ROOT}:/repo:ro",
            "aquasec/trivy:0.58.2",
            "fs",
            "--scanners",
            "vuln,secret,misconfig",
            "--skip-files",
            "/repo/.env",
            "--skip-dirs",
            "/repo/.cache",
            "--skip-dirs",
            "/repo/node_modules",
            "--skip-dirs",
            "/repo/.venv",
            "--exit-code",
            "1",
            "/repo",
        ],
    )
    print("PASS: every configured dependency, history, and filesystem scanner exited 0.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired as error:
        print(f"BLOCKED: {error.cmd[0]} exceeded the security scan time limit.", file=sys.stderr)
        raise SystemExit(2) from None
