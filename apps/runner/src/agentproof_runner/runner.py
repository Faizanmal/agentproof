"""Broker-invoked single-run Chromium executor; never imported by the web API."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from .actions import run_action
from .assertions import verdict
from .contracts import Manifest
from .event_emitter import emit
from .observations import Observations
from .observations import screenshot as capture_screenshot
from .policy import PolicyDenied, install_request_guards


def execute_manifest(
    manifest: Manifest, *, artifact_dir: Path | None = None, cancelled: Any = None
) -> tuple[int, dict[str, Any]]:
    root = artifact_dir or Path("/tmp/agentproof-artifacts")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + manifest.max_duration_seconds
    records: list[dict[str, Any]] = []
    observations = Observations()
    emit("sandbox.started", run_id=manifest.run_id, sandbox_id=manifest.sandbox_id)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                timezone_id="UTC",
                color_scheme="light",
                reduced_motion="reduce",
                accept_downloads=False,
                ignore_https_errors=False,
                service_workers="block",
            )
            install_request_guards(context, manifest, cancelled=cancelled)
            page = context.new_page()
            observations.attach(page)
            context.on("page", lambda p: observations.attach(p) if p is not page else None)
            for sequence, step in enumerate(manifest.plan.steps, 1):
                if cancelled is not None and cancelled.is_set():
                    emit("sandbox.cancelled", sequence=sequence)
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("run wall clock budget exhausted")
                started = time.monotonic()
                record: dict[str, Any] = {
                    "sequence": sequence,
                    "started_at": datetime.now(UTC).isoformat(),
                    "step_id": step.id,
                    "action": step.action,
                    "policy_decision": "ALLOW",
                    "status": "PASS",
                }
                try:
                    emit(
                        "sandbox.step.started",
                        sequence=sequence,
                        step_id=step.id,
                        action=step.action,
                    )
                    record.update(run_action(page, manifest, step, root))
                    artifact = record.get("artifact")
                    if isinstance(artifact, dict):
                        emit("sandbox.artifact", **artifact, step_id=step.id, sequence=sequence)
                    assertion = record.get("assertion")
                    if assertion and assertion.get("status") == "FAIL":
                        record["status"] = "FAIL"
                    record["observation"] = observations.capture(page)
                    record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                    records.append(record)
                    emit(
                        "sandbox.step",
                        sequence=sequence,
                        step_id=step.id,
                        action=step.action,
                        status=record.get("assertion", {}).get("status", "PASS"),
                        elapsed_ms=record["elapsed_ms"],
                        assertion=record.get("assertion"),
                        page_url=record.get("observation", {}).get("url"),
                    )
                except PolicyDenied as exc:
                    artifact = _failure_screenshot(
                        page, root, step.timeout_ms, manifest.max_artifact_bytes
                    )
                    record.update(
                        {
                            "status": "ERROR",
                            "policy_decision": "DENY",
                            "error_code": exc.reason,
                            "observation": observations.capture(page),
                        }
                    )
                    if artifact:
                        record["artifact"] = artifact
                        emit("sandbox.artifact", **artifact, step_id=step.id, sequence=sequence)
                    records.append(record)
                    emit(
                        "sandbox.step",
                        sequence=sequence,
                        step_id=step.id,
                        action=step.action,
                        status="ERROR",
                        error_code=exc.reason,
                        page_url=record["observation"].get("url"),
                    )
                    emit("sandbox.policy_denied", sequence=sequence, reason=exc.reason)
                    break
                except PlaywrightTimeout:
                    artifact = _failure_screenshot(
                        page, root, step.timeout_ms, manifest.max_artifact_bytes
                    )
                    record.update(
                        {
                            "status": "ERROR",
                            "error_code": "STEP_TIMEOUT",
                            "observation": observations.capture(page),
                        }
                    )
                    if artifact:
                        record["artifact"] = artifact
                        emit("sandbox.artifact", **artifact, step_id=step.id, sequence=sequence)
                    records.append(record)
                    emit(
                        "sandbox.step",
                        sequence=sequence,
                        step_id=step.id,
                        action=step.action,
                        status="ERROR",
                        error_code="STEP_TIMEOUT",
                        page_url=record["observation"].get("url"),
                    )
                    break
                except Exception as exc:
                    artifact = _failure_screenshot(
                        page, root, step.timeout_ms, manifest.max_artifact_bytes
                    )
                    record.update(
                        {
                            "status": "ERROR",
                            "error_code": type(exc).__name__,
                            "observation": observations.capture(page),
                        }
                    )
                    if artifact:
                        record["artifact"] = artifact
                        emit("sandbox.artifact", **artifact, step_id=step.id, sequence=sequence)
                    records.append(record)
                    emit(
                        "sandbox.step",
                        sequence=sequence,
                        step_id=step.id,
                        action=step.action,
                        status="ERROR",
                        error_code=record["error_code"],
                        page_url=record["observation"].get("url"),
                    )
                    break
            if not (cancelled is not None and cancelled.is_set()):
                try:
                    final_artifact = capture_screenshot(page, root, 3000)
                    if final_artifact["bytes"] <= manifest.max_artifact_bytes:
                        emit(
                            "sandbox.artifact",
                            **{
                                key: value for key, value in final_artifact.items() if key != "path"
                            },
                            step_id=manifest.plan.steps[-1].id if manifest.plan.steps else None,
                            sequence=len(records),
                        )
                    else:
                        Path(final_artifact["path"]).unlink(missing_ok=True)
                except Exception:
                    # The report records the absence of a screenshot; it never
                    # fabricates an artifact when sensitive text is detected.
                    emit("sandbox.policy_denied", reason="SCREENSHOT_UNAVAILABLE")
            context.close()
            browser.close()
        was_cancelled = cancelled is not None and cancelled.is_set()
        result = {
            "schema_version": "agentproof.run-trace/1.0",
            "run_id": manifest.run_id,
            "verdict": "INCONCLUSIVE" if was_cancelled else verdict(records),
            "steps": records,
            "observations": {
                "console_errors": observations.console_errors[-30:],
                "page_errors": observations.page_errors[-30:],
                "failed_requests": observations.failed_requests[-30:],
            },
        }
        _write_trace(root, manifest.run_id, result)
        emit(
            "sandbox.completed",
            run_id=manifest.run_id,
            verdict=result["verdict"],
            steps=len(records),
        )
        return (130 if was_cancelled else (0 if result["verdict"] == "PASS" else 1), result)
    except PolicyDenied as exc:
        result = {
            "schema_version": "agentproof.run-trace/1.0",
            "run_id": manifest.run_id,
            "verdict": "ERROR",
            "steps": records,
            "error_code": exc.reason,
        }
        _write_trace(root, manifest.run_id, result)
        emit("sandbox.failed", code=exc.reason)
        return 3, result
    except Exception as exc:
        result = {
            "schema_version": "agentproof.run-trace/1.0",
            "run_id": manifest.run_id,
            "verdict": "ERROR",
            "steps": records,
            "error_code": type(exc).__name__,
        }
        _write_trace(root, manifest.run_id, result)
        emit("sandbox.failed", code=type(exc).__name__)
        return 1, result


def _write_trace(root: Path, run_id: str, result: dict[str, Any]) -> None:
    safe_id = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")[:80] or str(uuid4())
    path = root / f"{safe_id}.json"
    safe_result = dict(result)
    safe_steps = []
    for step in result.get("steps", []):
        safe_step = dict(step)
        artifact = safe_step.get("artifact")
        if isinstance(artifact, dict):
            artifact = dict(artifact)
            artifact.pop("path", None)
            safe_step["artifact"] = artifact
        safe_steps.append(safe_step)
    safe_result["steps"] = safe_steps
    encoded = json.dumps(safe_result, separators=(",", ":"), ensure_ascii=True).encode()
    if len(encoded) <= 2_000_000:
        path.write_bytes(encoded)
    else:
        emit("sandbox.policy_denied", reason="TRACE_SIZE_LIMIT")


def _failure_screenshot(
    page: Any, root: Path, timeout_ms: int, max_bytes: int
) -> dict[str, Any] | None:
    try:
        artifact = capture_screenshot(page, root, timeout_ms)
        path = Path(artifact.pop("path"))
        if artifact["bytes"] > max_bytes:
            path.unlink(missing_ok=True)
            return None
        return artifact
    except Exception:
        return None
