"""Report real sandbox containment availability without substituting mocks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _broker_source() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "broker"
        / "src"
        / "agentproof_broker"
        / "main.py"
    )


def _source_claims_ok(source: str) -> list[str]:
    required = (
        "--read-only",
        "--cap-drop=ALL",
        "no-new-privileges",
        "10001:10001",
        "sandbox.cleaned_up",
    )
    return [flag for flag in required if flag not in source]


def main() -> int:
    docker = shutil.which("docker")
    if docker is None:
        print("BLOCKED: Docker CLI is not installed; no sandbox runtime was inspected.")
        return 2
    result = subprocess.run(
        [docker, "info", "--format", "{{.OSType}}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode or result.stdout.strip() != "linux":
        print("BLOCKED: a reachable Docker Linux Engine is required for runtime inspection.")
        return 2

    broker = _broker_source()
    if not broker.is_file():
        print("BLOCKED: broker source is missing; cannot reconcile containment claims.")
        return 2
    missing = _source_claims_ok(broker.read_text(encoding="utf-8"))
    if missing:
        print("BLOCKED: broker source is missing containment flags: " + ", ".join(missing))
        return 2

    print("CHECK: Docker Linux Engine is reachable.")
    print(
        "CHECK: broker source includes nonroot, read-only, cap-drop, no-new-privileges, "
        "and cleanup event markers."
    )
    print(
        "BLOCKED: packet-tested dedicated egress gateway/runtime profile is not installed "
        "in this environment; the broker rejects routable sandbox networks by default. "
        "No target, metadata IP, or sentinel was contacted. gVisor is not claimed."
    )
    print(
        "OWNER ACTION: on the sandbox VM, run allowlisted fixture network setup and "
        "packet negatives (infra/vultr/scripts/setup_fixture_network.sh) before enabling "
        "BROKER_SANDBOX_NETWORK for Challenge 1."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
