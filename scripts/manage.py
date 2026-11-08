"""Portable repository commands; no implicit provisioning or browser execution."""

import argparse
import base64
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNPM = "pnpm.cmd" if os.name == "nt" else "pnpm"
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "infra/compose.dev.yml"]


def run(command: list[str], *, timeout: int = 600) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, timeout=timeout)


def uv(*arguments: str) -> list[str]:
    # Bootstrap owns dependency installation. Running commands from an already
    # synced workspace must not trigger network build isolation on every call.
    return ["uv", "run", "--project", ".", "--frozen", "--no-sync", *arguments]


def dev(api_port: int, web_port: int) -> None:
    os.environ["API_INTERNAL_URL"] = f"http://127.0.0.1:{api_port}"
    commands = [
        uv(
            "uvicorn",
            "agentproof_api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--reload",
            "--reload-dir",
            "apps/api/src",
            "--no-access-log",
        ),
        [PNPM, "--filter", "web", "dev", "--hostname", "127.0.0.1", "--port", str(web_port)],
        uv("arq", "agentproof_worker.main.WorkerSettings"),
    ]
    processes: list[subprocess.Popen[bytes]] = []

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT, start_new_session=os.name != "nt"))
        while True:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError("A development service exited; stopping all services.")
            try:
                processes[0].wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "task",
        choices=[
            "bootstrap",
            "dev",
            "db-up",
            "migrate",
            "test",
            "lint",
            "typecheck",
            "build",
            "smoke",
            "contracts",
            "integration",
            "e2e",
            "security",
            "security-audit",
            "format",
            "quality-gate",
            "containment",
            "restore-test",
            "emergency-stop",
            "demo-seed",
            "demo-run",
            "demo-reset",
            "demo-fixture-bug-on",
            "demo-fixture-bug-off",
            "demo-containment",
        ],
    )
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=3000)
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.api_port, args.web_port)):
        parser.error("Ports must be between 1 and 65535")
    os.environ.setdefault("UV_CACHE_DIR", str(ROOT / ".cache/uv"))
    os.environ.setdefault("UV_TOOL_DIR", str(ROOT / ".cache/uv/tools"))
    os.environ.setdefault("npm_config_cache", str(ROOT / ".cache/npm"))
    workspace_sources = [
        str(ROOT / "apps" / package / "src") for package in ("api", "worker", "runner", "broker")
    ]
    existing_pythonpath = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        workspace_sources + ([existing_pythonpath] if existing_pythonpath else [])
    )
    if args.task == "bootstrap":
        run([PNPM, "install", "--frozen-lockfile"])
        run(["uv", "sync", "--frozen", "--all-packages"])
        if not (ROOT / ".env").exists():
            shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        env_path = ROOT / ".env"
        env_text = env_path.read_text(encoding="utf-8")
        if (
            os.environ.get("APP_ENV", "development") != "production"
            and re.search(r"(?m)^APP_ENV\s*=\s*[\"']?production", env_text) is None
        ):
            for key, value in (
                ("SESSION_SIGNING_KEY", secrets.token_urlsafe(48)),
                ("MASTER_ENCRYPTION_KEY", base64.b64encode(secrets.token_bytes(32)).decode()),
            ):
                env_text = re.sub(r"(?m)^" + key + r"=[ \t]*$", key + "=" + value, env_text)
            env_path.write_text(env_text, encoding="utf-8")
            if os.name != "nt":
                env_path.chmod(0o600)
    elif args.task == "dev":
        dev(args.api_port, args.web_port)
    elif args.task == "db-up":
        run([*COMPOSE, "up", "-d", "--wait", "redis"], timeout=180)
    elif args.task == "migrate":
        run(uv("alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"))
    elif args.task == "test":
        run(uv("pytest"))
        run(uv("python", "scripts/export_contracts.py", "--check"))
    elif args.task == "lint":
        run(uv("ruff", "check", "apps", "scripts", "tests", "pyproject.toml"))
        run(uv("ruff", "format", "--check", "apps", "scripts", "tests", "pyproject.toml"))
        run([PNPM, "lint"])
        run([PNPM, "format:check"])
    elif args.task == "typecheck":
        run(uv("mypy"))
        run([PNPM, "typecheck"])
    elif args.task == "build":
        run([PNPM, "--filter", "web", "build"])
        for package in ("api", "worker", "runner", "broker"):
            run(["uv", "build", "--package", f"agentproof-{package}", "--out-dir", ".cache/dist"])
    elif args.task == "integration":
        run(uv("python", "scripts/integration.py"), timeout=900)
    elif args.task == "e2e":
        run(uv("python", "scripts/integration.py", "--e2e"), timeout=1200)
    elif args.task == "contracts":
        run(uv("python", "scripts/export_contracts.py"))
        run([PNPM, "--filter", "@agentproof/contracts", "generate"])
    elif args.task == "smoke":
        run(
            uv(
                "python",
                "scripts/smoke.py",
                "--api-port",
                str(args.api_port),
                "--web-port",
                str(args.web_port),
            ),
            timeout=90,
        )
    elif args.task == "security":
        run(uv("python", "scripts/security_check.py"))
    elif args.task == "security-audit":
        run(uv("python", "scripts/security_audit.py"), timeout=900)
    elif args.task == "containment":
        run([sys.executable, "scripts/containment_check.py"], timeout=45)
    elif args.task == "restore-test":
        run(uv("python", "scripts/restore_test.py"), timeout=600)
    elif args.task == "emergency-stop":
        run(uv("python", "scripts/emergency_stop.py"), timeout=45)
    elif args.task == "quality-gate":
        run(uv("ruff", "check", "apps", "scripts", "tests", "pyproject.toml"))
        run(uv("ruff", "format", "--check", "apps", "scripts", "tests", "pyproject.toml"))
        run([PNPM, "lint"])
        run(uv("mypy"))
        run([PNPM, "typecheck"])
        run(
            uv(
                "--with",
                "pytest-cov==7.0.0",
                "pytest",
                "--cov=agentproof_api",
                "--cov=agentproof_worker",
                "--cov=agentproof_runner",
                "--cov=agentproof_broker",
            )
        )
        run(uv("python", "scripts/export_contracts.py", "--check"))
        run(uv("python", "scripts/security_check.py"))
        run([PNPM, "--filter", "web", "build"], timeout=600)
    elif args.task.startswith("demo-"):
        commands = {
            "demo-seed": "seed",
            "demo-run": "run",
            "demo-reset": "reset",
            "demo-fixture-bug-on": "bug-on",
            "demo-fixture-bug-off": "bug-off",
            "demo-containment": "containment",
        }
        command = [*uv("python", "scripts/demo.py", commands[args.task])]
        if args.task == "demo-reset" and os.environ.get("DEMO_RUN_ID"):
            command.extend(["--run-id", os.environ["DEMO_RUN_ID"]])
        run(command, timeout=900 if args.task == "demo-run" else 120)
    elif args.task == "format":
        run(uv("ruff", "check", "--fix", "."))
        run(uv("ruff", "format", "."))
        run([PNPM, "format"])


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as error:
        print(f"Command failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
