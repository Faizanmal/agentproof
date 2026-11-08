"""Deterministic checks after every model response, independent of model risk labels."""

from dataclasses import dataclass

from fastapi import HTTPException

from agentproof_api.plan_schema import QAPlanV1, TestScope
from agentproof_api.policy import Action, TargetPolicy, check_action, path_glob_allowed
from agentproof_api.settings import Settings
from agentproof_api.targets import demo_allowed


class PlanRejected(Exception):
    def __init__(self, code: str = "PLAN_POLICY_REJECTED") -> None:
        self.code = code
        super().__init__(code)


@dataclass
class RiskResult:
    plan: QAPlanV1
    reasons: list[str]


PROHIBITED = {
    "payment",
    "purchase",
    "payout",
    "checkout",
    "delete",
    "deletion",
    "invite",
    "send",
    "email message",
    "password reset",
    "export",
    "unsubscribe",
    "transfer",
    "remove account",
}


def assess(
    plan: QAPlanV1,
    goal: str,
    scope: TestScope,
    policy: TargetPolicy,
    base_origin: str,
    config: Settings,
) -> RiskResult:
    if plan.goal != goal:
        raise PlanRejected("PLAN_GOAL_CHANGED")
    if base_origin not in policy.authorized_origins or len(plan.steps) > policy.max_actions:
        raise PlanRejected("PLAN_SCOPE_DENIED")
    if scope.credential_ref_id and scope.credential_ref_id not in policy.credential_ref_ids:
        raise PlanRejected("CREDENTIAL_SCOPE_DENIED")
    if sum(s.timeout_ms for s in plan.steps) > policy.max_duration_seconds * 1000:
        raise PlanRejected("PLAN_DURATION_EXCEEDED")
    if sum(s.action == "navigate" for s in plan.steps) > policy.max_pages:
        raise PlanRejected("PLAN_PAGE_BUDGET_EXCEEDED")
    path = scope.start_path
    reasons: set[str] = set()
    steps = []
    for step in plan.steps:
        if step.url_path is not None:
            path = step.url_path
        paths = [path]
        if step.action == "assert_url" and step.assertion is not None:
            paths.append(str(step.assertion.expected))
        for candidate in paths:
            if not path_glob_allowed(candidate, scope.allowed_paths):
                raise PlanRejected("PLAN_PATH_OUTSIDE_TEST_SCOPE")
            try:
                check_action(policy, Action(url=base_origin + candidate, action="read"), config)
            except HTTPException:
                raise PlanRejected("PLAN_PATH_DENIED") from None
        # Targets are not executable selectors/programs. Still reject prohibited effects by
        # deterministic control rules; ambiguous controls require explicit step approval.
        name = step.target.name.casefold() if step.target else ""
        if step.action in {"click", "fill", "select", "check", "uncheck"}:
            if any(word in name for word in PROHIBITED):
                raise PlanRejected("PROHIBITED_SIDE_EFFECT")
            if step.value_ref and step.value_ref.startswith("credential:"):
                # Broker credential delivery is not wired for runner execution yet.
                # Reject rather than approve a plan that cannot run honestly.
                raise PlanRejected("AUTHENTICATED_TESTING_UNSUPPORTED")
            fixture = (
                demo_allowed(base_origin, config)
                and policy.side_effect_rules.fixture_forms
                and path_glob_allowed(path, policy.side_effect_rules.fixture_paths)
            )
            if step.action in {"fill", "select", "check", "uncheck"} and not fixture:
                raise PlanRejected("FIXTURE_PERMISSION_REQUIRED")
            if step.action == "click":
                # Non-fixture clicks require an exact selector reviewed in TargetPolicy.
                if not fixture and not (
                    step.target
                    and step.target.strategy == "css"
                    and step.target.name in policy.safe_click_selectors
                ):
                    raise PlanRejected("UNREVIEWED_CLICK")
                reasons.add("INTERACTIVE_CONTROL_REQUIRES_REVIEW")
        if step.target and step.target.nth is not None:
            reasons.add("EXPLICIT_LOCATOR_DISAMBIGUATION")
        needs = step.requires_approval or step.action in {
            "click",
            "fill",
            "select",
            "check",
            "uncheck",
        }
        steps.append(step.model_copy(update={"requires_approval": needs}))
    risk = (
        "HIGH"
        if reasons or plan.risk_level == "HIGH"
        else ("MEDIUM" if plan.risk_level in {"MEDIUM", "HIGH"} else "LOW")
    )
    return RiskResult(plan.model_copy(update={"steps": steps, "risk_level": risk}), sorted(reasons))
