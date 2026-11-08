"""CLI entry point used only by the disposable sandbox image."""

from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import threading
from pathlib import Path

__all__ = ["_host_allowed", "_path_allowed", "socket"]


def _host_allowed(url: str, origins: list[str]) -> bool:
    from .policy import host_allowed

    return host_allowed(url, origins)


def _path_allowed(url: str, patterns: list[str]) -> bool:
    from .policy import path_allowed

    return path_allowed(url, patterns)


def execute(path: Path) -> int:
    from pydantic import ValidationError

    from .contracts import Manifest
    from .event_emitter import emit

    try:
        manifest = Manifest.model_validate_json(path.read_bytes())
    except (ValidationError, OSError, ValueError):
        emit("sandbox.failed", code="INVALID_MANIFEST")
        return 2
    if manifest.plan.schema_version != "1.0" or len(manifest.plan.steps) > manifest.max_actions:
        emit("sandbox.failed", code="PLAN_LIMIT_EXCEEDED")
        return 2
    cancelled = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        cancelled.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    from .runner import execute_manifest

    code, _result = execute_manifest(manifest, cancelled=cancelled)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one broker-validated QAPlanV1")
    parser.add_argument("--manifest", default="/tmp/agentproof-manifest.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        print(
            json.dumps(
                {
                    "component": "runner",
                    "executor": "playwright-chromium",
                    "check_only": True,
                    "execution_enabled": False,
                }
            )
        )
        return 0
    return execute(Path(args.manifest))


if __name__ == "__main__":
    sys.exit(main())
