"""Report real sandbox containment availability without substituting mocks."""

from __future__ import annotations

import shutil
import subprocess


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
    print("Docker Linux Engine is reachable, but network containment is intentionally disabled.")
    print(
        "BLOCKED: no packet-tested dedicated egress gateway/runtime profile is installed; "
        "the broker rejects routable sandbox networks. No target, metadata IP, "
        "or sentinel was contacted."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
