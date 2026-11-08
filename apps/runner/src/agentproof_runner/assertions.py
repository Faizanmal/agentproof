"""Playwright-observed assertions and honest run verdict aggregation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .contracts import Step
from .locators import locate
from .redaction import redact_text


def observe_assertion(page: Any, step: Step) -> dict[str, Any]:
    assert step.assertion is not None
    expected = step.assertion.expected
    observed: Any
    if step.action == "assert_url":
        observed = (
            page.url.split("?", 1)[0]
            .split("#", 1)[0]
            .removeprefix(
                page.url.split("://", 1)[0] + "://" + page.url.split("://", 1)[1].split("/", 1)[0]
            )
            or "/"
        )
    else:
        if step.target is None:
            raise ValueError("assertion locator required")
        loc = locate(page, step.target, allow_multiple=step.action == "assert_count")
        if step.assertion.kind == "visible":
            observed = loc.is_visible() if loc.count() else False
        elif step.assertion.kind == "text":
            observed = redact_text(loc.inner_text(timeout=step.timeout_ms))
        elif step.assertion.kind == "count":
            observed = loc.count()
        else:
            observed = None
    matched = observed == expected
    return {
        "status": "PASS" if matched else "FAIL",
        "kind": step.assertion.kind,
        "expected": redact_text(expected) if isinstance(expected, str) else expected,
        "observed": observed,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def verdict(records: list[dict[str, Any]]) -> str:
    assertions = [r for r in records if r.get("assertion")]
    if any(r["assertion"].get("status") == "FAIL" for r in assertions):
        return "FAIL"
    if any(r.get("status") == "ERROR" for r in records):
        return "ERROR"
    if not assertions or any(r["assertion"].get("status") != "PASS" for r in assertions):
        return "INCONCLUSIVE"
    return "PASS"
