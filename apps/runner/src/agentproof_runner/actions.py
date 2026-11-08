"""Typed Playwright action executor with bounded same-locator retries."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .assertions import observe_assertion
from .contracts import Manifest, Step
from .event_emitter import emit
from .locators import locate
from .navigator import navigate
from .observations import screenshot
from .policy import PolicyDenied, guard_action

_VALUES = {
    "synthetic:email": "agentproof-test@example.invalid",
    "synthetic:option": "test",
    "synthetic:yearly": "yearly",
}
_UNSAFE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)")


def run_action(page: Any, manifest: Manifest, step: Step, artifact_dir: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "step_id": step.id,
        "action": step.action,
        "policy_decision": "ALLOW",
        "attempts": [],
    }
    for attempt in range(1, 4):
        try:
            if step.action == "navigate":
                navigate(page, manifest, step)
            elif step.action == "screenshot":
                artifact = screenshot(page, artifact_dir, step.timeout_ms)
                if artifact["bytes"] > manifest.max_artifact_bytes:
                    from pathlib import Path

                    Path(artifact["path"]).unlink(missing_ok=True)
                    raise PolicyDenied("artifact size exceeded")
                artifact.pop("path", None)
                record["artifact"] = artifact
            elif step.action.startswith("assert_"):
                record["assertion"] = observe_assertion(page, step)
            else:
                if step.target is None:
                    raise PolicyDenied("action requires locator")
                loc = locate(page, step.target)
                if step.action == "click":
                    guard_action(page, loc, step.action, manifest)
                    loc.click(timeout=step.timeout_ms)
                elif step.action in {"fill", "select"}:
                    if (
                        not step.value_ref
                        or step.value_ref.startswith("credential:")
                        or _UNSAFE.search(step.value_ref)
                    ):
                        raise PolicyDenied("credential reference unavailable or invalid")
                    value = _VALUES.get(step.value_ref)
                    if step.value_ref in {"synthetic:name", "synthetic:text"}:
                        value = f"AgentProof-E2E-{manifest.run_id}"
                    if value is None:
                        raise PolicyDenied("unknown synthetic value reference")
                    if step.action == "fill":
                        loc.fill(value, timeout=step.timeout_ms)
                    else:
                        loc.select_option(value, timeout=step.timeout_ms)
                elif step.action == "check":
                    loc.check(timeout=step.timeout_ms)
                elif step.action == "uncheck":
                    loc.uncheck(timeout=step.timeout_ms)
                elif step.action == "wait_for":
                    loc.wait_for(state="visible", timeout=step.timeout_ms)
                else:
                    raise PolicyDenied("unsupported action")
            record["attempts"].append({"attempt": attempt, "status": "PASS"})
            return record
        except PlaywrightTimeout:
            record["attempts"].append({"attempt": attempt, "status": "TIMEOUT"})
            if attempt == 3:
                raise
            emit("sandbox.retry", step_id=step.id, attempt=attempt, reason="TRANSIENT_TIMEOUT")
    raise RuntimeError("action retry exhaustion")
