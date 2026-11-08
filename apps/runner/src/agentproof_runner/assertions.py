"""Playwright-observed assertions and honest run verdict aggregation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .contracts import Step
from .locators import locate, resolve_locator
from .policy import PolicyDenied
from .redaction import redact_text


def observe_assertion(page: Any, step: Step) -> dict[str, Any]:
    assert step.assertion is not None
    expected = step.assertion.expected
    observed: Any
    capture_note: str | None = None
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
        if step.assertion.kind == "count":
            loc = locate(page, step.target, allow_multiple=True)
            observed = loc.count()
        else:
            # Zero matches are assertion FAILs. Ambiguous matches stay infrastructure ERROR.
            loc, match_count = resolve_locator(page, step.target)
            if match_count == 0:
                observed = False if step.assertion.kind == "visible" else None
                capture_note = "element_not_found"
            elif match_count != 1:
                raise PolicyDenied(
                    "locator must match exactly one element", details={"match_count": match_count}
                )
            elif step.assertion.kind == "visible":
                observed = loc.is_visible()
            elif step.assertion.kind == "text":
                observed = redact_text(loc.inner_text(timeout=step.timeout_ms))
            else:
                observed = None
                capture_note = "unsupported_assertion_kind"
    # Missing elements keep observed as None/False — never invent captured text.
    matched = observed == expected and capture_note != "unsupported_assertion_kind"
    result: dict[str, Any] = {
        "status": "PASS" if matched else "FAIL",
        "kind": step.assertion.kind,
        "expected": redact_text(expected) if isinstance(expected, str) else expected,
        "observed": observed,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if capture_note:
        result["capture"] = capture_note
    return result


def verdict(records: list[dict[str, Any]]) -> str:
    assertions = [r for r in records if r.get("assertion")]
    if any(r["assertion"].get("status") == "FAIL" for r in assertions):
        return "FAIL"
    if any(r.get("status") == "ERROR" for r in records):
        return "ERROR"
    if not assertions or any(r["assertion"].get("status") != "PASS" for r in assertions):
        return "INCONCLUSIVE"
    return "PASS"
