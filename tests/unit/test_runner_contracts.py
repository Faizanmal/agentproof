from __future__ import annotations

import pytest
from agentproof_runner.assertions import verdict
from agentproof_runner.contracts import Assertion, Step, Target
from agentproof_runner.redaction import redact_text, redact_url
from pydantic import ValidationError


def test_css_subset_rejects_selector_expressions() -> None:
    with pytest.raises(ValidationError):
        Target(strategy="css", name="body *", role=None)


def test_observation_redaction_removes_pii_and_sensitive_url_segments() -> None:
    text = redact_text("Contact qa.user@example.test, phone +1 415 555 0100")
    url = redact_url("https://site.example/reset/private-token?email=qa.user@example.test")
    assert "qa.user@example.test" not in text + url
    assert "555 0100" not in text + url
    assert "private-token" not in url
    assert "[REDACTED]" in url


def test_plan_step_rejects_absolute_navigation_and_unknown_actions() -> None:
    with pytest.raises(ValidationError):
        Step(id="s1", action="navigate", url_path="https://evil.invalid", requires_approval=False)
    with pytest.raises(ValidationError):
        Step(id="s1", action="evaluate", requires_approval=False)


def test_redaction_removes_query_and_secret_assignments() -> None:
    assert "private" not in redact_url("https://example.test/path?token=private")
    assert "topsecret" not in redact_text("password=topsecret")


def test_verdict_requires_observed_assertion() -> None:
    assert verdict([{"status": "PASS"}]) == "INCONCLUSIVE"
    assert verdict([{"assertion": {"status": "FAIL"}}]) == "FAIL"
    assert verdict([{"assertion": {"status": "PASS"}}]) == "PASS"
    assert verdict([{"status": "ERROR", "assertion": {"status": "PASS"}}]) == "ERROR"


def test_assertion_contract_rejects_non_boolean_visibility() -> None:
    with pytest.raises(ValidationError):
        Assertion(kind="visible", expected="yes")


def test_missing_heading_is_assertion_fail_not_policy_error() -> None:
    from types import SimpleNamespace

    from agentproof_runner.assertions import observe_assertion

    class _EmptyLocator:
        def count(self) -> int:
            return 0

        def is_visible(self) -> bool:
            raise AssertionError("must not probe visibility when absent")

        def inner_text(self, timeout: int = 0) -> str:
            raise AssertionError("must not invent text when absent")

    page = SimpleNamespace()
    step = Step(
        id="s1",
        action="assert_text",
        target=Target(
            strategy="role",
            role="heading",
            name="Judge audit nonexistent heading 20261108",
        ),
        assertion=Assertion(
            kind="text",
            expected="Judge audit nonexistent heading 20261108",
        ),
        requires_approval=False,
    )

    def fake_resolve(_page: object, _target: object) -> tuple[object, int]:
        return _EmptyLocator(), 0

    import agentproof_runner.assertions as assertions_mod

    original = assertions_mod.resolve_locator
    assertions_mod.resolve_locator = fake_resolve  # type: ignore[assignment]
    try:
        result = observe_assertion(page, step)
    finally:
        assertions_mod.resolve_locator = original  # type: ignore[assignment]
    assert result["status"] == "FAIL"
    assert result["expected"] == "Judge audit nonexistent heading 20261108"
    assert result["observed"] is None
    assert result["capture"] == "element_not_found"
    assert verdict([{"status": result["status"], "assertion": result}]) == "FAIL"
