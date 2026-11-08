"""Sanitized browser observations and event listeners."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .policy import PolicyDenied
from .redaction import redact_text, redact_url

_PII = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\.[A-Z]{2,}\b|(?:\+?\d[\d ().-]{7,}\d)")


class Observations:
    def __init__(self) -> None:
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.failed_requests: list[dict[str, str]] = []

    def attach(self, page: Any) -> None:
        page.on(
            "console",
            lambda msg: (
                self.console_errors.append(redact_text(msg.text)) if msg.type == "error" else None
            ),
        )
        page.on(
            "pageerror", lambda error: self.page_errors.append(redact_text(type(error).__name__))
        )
        page.on(
            "requestfailed",
            lambda req: self.failed_requests.append(
                {"url": redact_url(req.url), "failure": redact_text(req.failure or "unknown")}
            ),
        )

    def capture(self, page: Any) -> dict[str, Any]:
        try:
            title = redact_text(page.title())[:300]
        except Exception:
            title = ""
        try:
            excerpt = redact_text(page.locator("body").inner_text(timeout=1200))[:4000]
        except Exception:
            excerpt = ""
        try:
            roles = redact_text(page.locator("body").aria_snapshot(timeout=1200))[:6000]
        except Exception:
            roles = ""
        return {
            "captured_at": datetime.now(UTC).isoformat(),
            "url": redact_url(page.url),
            "title": title,
            "visible_roles_and_labels": roles,
            "visible_text_excerpt": excerpt,
            "console_errors": self.console_errors[-30:],
            "page_errors": self.page_errors[-30:],
            "failed_requests": self.failed_requests[-30:],
        }


def screenshot(page: Any, directory: Any, timeout: int) -> dict[str, Any]:
    import hashlib

    path = directory / f"{uuid4()}.png"
    try:
        visible_text = page.locator("body").inner_text(timeout=min(timeout, 1200))[:120_000]
    except Exception:
        visible_text = ""
    if _PII.search(visible_text):
        raise PolicyDenied("SCREENSHOT_EXCLUDED_PII_TEXT")
    masks = [
        page.locator("input:not([type=hidden]), textarea, [contenteditable=true]"),
        page.locator("[data-sensitive], [data-agentproof-private]"),
    ]
    page.screenshot(
        path=str(path), full_page=True, animations="disabled", timeout=timeout, mask=masks
    )
    data = path.read_bytes()
    return {
        "artifact_id": path.stem,
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mime_type": "image/png",
        "redaction_policy": "masked-inputs-no-detected-pii-v1",
    }
