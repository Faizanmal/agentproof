"""Versioned planner contract. No executable code or credential values."""

import re
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


def relative_path(value: str) -> str:
    parts = urlsplit(value)
    if (
        len(value) > 2048
        or not value.startswith("/")
        or value.startswith("//")
        or parts.scheme
        or parts.netloc
        or parts.query
        or parts.fragment
        or "%" in value
        or chr(92) in value
        or any(p in {".", ".."} for p in value.split("/"))
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError("Only plain relative paths are allowed")
    return value


class Locator(Contract):
    strategy: Literal["role", "label", "placeholder", "test_id", "text", "css"]
    role: (
        Literal[
            "button",
            "link",
            "textbox",
            "heading",
            "checkbox",
            "radio",
            "combobox",
            "option",
            "list",
            "listitem",
            "dialog",
            "alert",
            "navigation",
            "main",
            "tab",
        ]
        | None
    ) = None
    name: str = Field(min_length=1, max_length=200)
    require_unique: Literal[True] = True
    nth: int | None = Field(default=None, ge=0, le=9)

    @model_validator(mode="after")
    def valid_locator(self) -> Self:
        if self.strategy == "role" and self.role is None:
            raise ValueError("role is required")
        if self.strategy != "role" and self.role is not None:
            raise ValueError("role only belongs to role locators")
        if (
            self.strategy == "css"
            and re.fullmatch(
                r'body|main|h[1-3]|#[a-zA-Z][a-zA-Z0-9_-]{0,78}|\[data-testid="[a-zA-Z0-9_-]{1,80}"\]',
                self.name,
            )
            is None
        ):
            raise ValueError("CSS is limited to body/main/headings, ID or exact data-testid")
        return self


class Assertion(Contract):
    kind: Literal["visible", "text", "url", "count"]
    expected: str | bool | int = Field()

    @model_validator(mode="after")
    def correct_value(self) -> Self:
        if self.kind == "visible" and type(self.expected) is not bool:
            raise ValueError("visible requires a boolean")
        if self.kind == "count" and (
            type(self.expected) is not int or not 0 <= self.expected <= 1000
        ):
            raise ValueError("count requires a bounded integer")
        if self.kind in {"text", "url"}:
            if not isinstance(self.expected, str) or not 1 <= len(self.expected) <= 2000:
                raise ValueError("text/url requires bounded text")
            if self.kind == "url":
                relative_path(self.expected)
        return self


class PlanStep(Contract):
    id: str = Field(pattern=r"^s[1-9][0-9]?$", max_length=3)
    action: Literal[
        "navigate",
        "click",
        "fill",
        "select",
        "check",
        "uncheck",
        "wait_for",
        "assert_visible",
        "assert_text",
        "assert_url",
        "assert_count",
        "screenshot",
    ]
    url_path: str | None = Field(default=None, max_length=2048)
    target: Locator | None = None
    value_ref: (
        Literal[
            "credential:email",
            "credential:username",
            "credential:password",
            "synthetic:email",
            "synthetic:name",
            "synthetic:text",
            "synthetic:option",
            "synthetic:yearly",
        ]
        | None
    ) = None
    assertion: Assertion | None = None
    requires_approval: bool  # Deliberately required; the model cannot omit it.
    timeout_ms: int = Field(default=5000, ge=100, le=10000)

    @field_validator("url_path")
    @classmethod
    def path(cls, value: str | None) -> str | None:
        return relative_path(value) if value is not None else None

    @model_validator(mode="after")
    def action_shape(self) -> Self:
        if self.action == "navigate":
            if self.url_path is None or self.target is not None:
                raise ValueError("navigate requires only a relative path")
        elif self.url_path is not None:
            raise ValueError("Only navigate accepts url_path")
        if self.action not in {"navigate", "assert_url", "screenshot"} and self.target is None:
            raise ValueError("Action requires a stable locator")
        if (self.action in {"fill", "select"}) != (self.value_ref is not None):
            raise ValueError("Only fill/select requires a symbolic value reference")
        expected = {
            "assert_visible": "visible",
            "assert_text": "text",
            "assert_url": "url",
            "assert_count": "count",
        }
        if self.action in expected:
            if self.assertion is None or self.assertion.kind != expected[self.action]:
                raise ValueError("Action requires a matching assertion")
        elif self.assertion is not None:
            raise ValueError("Unexpected assertion")
        return self


class QAPlanV1(Contract):
    schema_version: Literal["1.0"]
    goal: str = Field(min_length=1, max_length=4000)
    assumptions: list[str] = Field(max_length=15)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    steps: list[PlanStep] = Field(min_length=1, max_length=15)
    success_criteria: list[str] = Field(min_length=1, max_length=15)
    warnings: list[str] = Field(max_length=15)

    @model_validator(mode="after")
    def bounded_unique(self) -> Self:
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("Step IDs must be unique")
        if self.steps[0].action != "navigate":
            raise ValueError("Begin with an explicit scoped navigation")
        if not any(s.action.startswith("assert_") for s in self.steps):
            raise ValueError("At least one observable assertion is required")
        if any(
            not 1 <= len(t) <= 1000
            for t in self.assumptions + self.success_criteria + self.warnings
        ):
            raise ValueError("Plan text must be bounded")
        return self


class TestScope(Contract):
    start_path: str = Field(default="/", max_length=2048)
    allowed_paths: list[str] = Field(default_factory=lambda: ["/*"], min_length=1, max_length=20)
    credential_ref_id: UUID | None = None

    @field_validator("start_path")
    @classmethod
    def path(cls, value: str) -> str:
        return relative_path(value)

    @field_validator("allowed_paths")
    @classmethod
    def paths(cls, values: list[str]) -> list[str]:
        for value in values:
            relative_path(value)
            if "[" in value or "?" in value:
                raise ValueError("Use simple absolute path globs")
        return values


class GeneratePlanInput(Contract):
    goal: str = Field(min_length=1, max_length=4000)
    scope: TestScope = Field(default_factory=TestScope)
    untrusted_page_text: str = Field(default="", max_length=8000)


class ApprovePlanInput(Contract):
    version: int = Field(ge=1)
    authorization_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_step_ids: list[str] = Field(default_factory=list, max_length=15)
    approve_high_risk: bool = False
