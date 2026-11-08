from __future__ import annotations

import pytest
from agentproof_api.plan_risk import PlanRejected, assess
from agentproof_api.plan_schema import Assertion, Locator, PlanStep, QAPlanV1
from agentproof_api.plan_schema import TestScope as PlanTestScope
from agentproof_api.policy import TargetPolicy
from agentproof_api.settings import Settings
from pydantic import SecretStr

ORIGIN = "http://127.0.0.1:19090"


def _config() -> Settings:
    return Settings(
        _env_file=None,
        demo_mode_enabled=True,
        demo_origins=[ORIGIN],
        session_signing_key=SecretStr("test-signature-key-" * 3),
    )


def _plan(*, value_ref: str | None = None, fill: bool = False) -> QAPlanV1:
    steps = [
        PlanStep(
            id="s1",
            action="navigate",
            url_path="/qa-fixture/",
            requires_approval=False,
        ),
    ]
    if fill:
        steps.extend(
            [
                PlanStep(
                    id="s2",
                    action="fill",
                    target=Locator(strategy="role", role="textbox", name="Email"),
                    value_ref=value_ref or "synthetic:text",
                    requires_approval=True,
                ),
                PlanStep(
                    id="s3",
                    action="assert_text",
                    target=Locator(
                        strategy="role",
                        role="heading",
                        name="AgentProof owned fixture",
                    ),
                    assertion=Assertion(
                        kind="text", expected="AgentProof owned fixture"
                    ),
                    requires_approval=False,
                ),
            ]
        )
    else:
        steps.append(
            PlanStep(
                id="s2",
                action="assert_text",
                target=Locator(
                    strategy="role",
                    role="heading",
                    name="AgentProof owned fixture",
                ),
                assertion=Assertion(kind="text", expected="AgentProof owned fixture"),
                requires_approval=False,
            )
        )
    return QAPlanV1(
        schema_version="1.0",
        goal="Open the owned fixture and confirm the heading",
        assumptions=[],
        risk_level="LOW",
        steps=steps,
        success_criteria=["Heading visible"],
        warnings=[],
    )


def test_credential_refs_are_unsupported_not_silently_approved() -> None:
    policy = TargetPolicy(authorized_origins=[ORIGIN], credential_ref_ids=[])
    scope = PlanTestScope(start_path="/qa-fixture/", allowed_paths=["/qa-fixture/", "/*"])
    with pytest.raises(PlanRejected) as rejected:
        assess(
            _plan(fill=True, value_ref="credential:password"),
            "Open the owned fixture and confirm the heading",
            scope,
            policy,
            ORIGIN,
            _config(),
        )
    assert rejected.value.code == "AUTHENTICATED_TESTING_UNSUPPORTED"


def test_fixture_forms_required_for_fill_on_static_scope() -> None:
    policy = TargetPolicy(authorized_origins=[ORIGIN])
    scope = PlanTestScope(start_path="/qa-fixture/", allowed_paths=["/qa-fixture/", "/*"])
    with pytest.raises(PlanRejected) as rejected:
        assess(
            _plan(fill=True, value_ref="synthetic:text"),
            "Open the owned fixture and confirm the heading",
            scope,
            policy,
            ORIGIN,
            _config(),
        )
    assert rejected.value.code == "FIXTURE_PERMISSION_REQUIRED"


def test_heading_only_plan_is_accepted_for_static_fixture() -> None:
    policy = TargetPolicy(authorized_origins=[ORIGIN])
    scope = PlanTestScope(start_path="/qa-fixture/", allowed_paths=["/qa-fixture/", "/*"])
    result = assess(
        _plan(),
        "Open the owned fixture and confirm the heading",
        scope,
        policy,
        ORIGIN,
        _config(),
    )
    assert result.plan.goal == "Open the owned fixture and confirm the heading"
    assert result.plan.risk_level == "LOW"
