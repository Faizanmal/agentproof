"""Bounded navigation using Playwright navigation events and policy checks."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from .contracts import Manifest, Step
from .event_emitter import emit
from .policy import PolicyDenied, host_allowed, path_allowed


def navigate(page: Any, manifest: Manifest, step: Step) -> None:
    if not step.url_path:
        raise PolicyDenied("navigate requires relative path")
    target = urljoin(manifest.origins[0].rstrip("/") + "/", step.url_path.lstrip("/"))
    if not host_allowed(target, manifest.origins) or not path_allowed(
        target, manifest.allowed_paths
    ):
        emit("sandbox.policy_denied", reason="NAVIGATION_OUT_OF_SCOPE")
        raise PolicyDenied("navigation denied")
    response = page.goto(target, wait_until="domcontentloaded", timeout=step.timeout_ms)
    if response and response.status >= 500:
        raise RuntimeError("target returned server error")
    if not host_allowed(page.url, manifest.origins) or not path_allowed(
        page.url, manifest.allowed_paths
    ):
        emit("sandbox.policy_denied", reason="FINAL_URL_OUT_OF_SCOPE")
        raise PolicyDenied("redirect denied")
