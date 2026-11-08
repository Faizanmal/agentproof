"""Strict, bounded QA plan and broker manifest contracts."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Target(Strict):
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
    require_unique: bool = True
    nth: int | None = Field(default=None, ge=0, le=9)

    @model_validator(mode="after")
    def safe_selector(self) -> Target:
        if (self.strategy == "role") != (self.role is not None):
            raise ValueError("role locator shape is invalid")
        if self.strategy == "css" and not (
            self.name in {"body", "main", "h1", "h2", "h3"}
            or re.fullmatch(r"#[A-Za-z][A-Za-z0-9_-]{0,78}", self.name)
            or re.fullmatch(r'\[data-testid="[A-Za-z0-9_-]{1,80}"\]', self.name)
        ):
            raise ValueError("CSS locator is outside the allowed subset")
        return self


class Assertion(Strict):
    kind: Literal["visible", "text", "url", "count"]
    expected: str | bool | int

    @model_validator(mode="after")
    def bounded_value(self) -> Assertion:
        if self.kind == "visible" and type(self.expected) is not bool:
            raise ValueError("visibility assertion requires a boolean")
        if self.kind == "count" and (
            type(self.expected) is not int or not 0 <= self.expected <= 1000
        ):
            raise ValueError("count assertion requires a bounded integer")
        if self.kind in {"text", "url"}:
            if not isinstance(self.expected, str) or not 1 <= len(self.expected) <= 2000:
                raise ValueError("text and URL assertions require bounded strings")
        if self.kind == "url":
            if not isinstance(self.expected, str):
                raise ValueError("URL assertions require a string")
            if (
                not self.expected.startswith("/")
                or self.expected.startswith("//")
                or ":" in self.expected
                or "%" in self.expected
                or "?" in self.expected
                or "#" in self.expected
                or "\\" in self.expected
            ):
                raise ValueError("URL assertions require a plain relative path")
        return self


class Step(Strict):
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
    url_path: str | None = None
    target: Target | None = None
    value_ref: str | None = None
    assertion: Assertion | None = None
    requires_approval: bool
    timeout_ms: int = Field(default=5000, ge=100, le=10000)

    @field_validator("url_path")
    @classmethod
    def relative_path_only(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith("/")
            or value.startswith("//")
            or ":" in value
            or "\\" in value
            or "?" in value
            or "#" in value
            or "%" in value
            or any(piece in {".", ".."} for piece in value.split("/"))
        ):
            raise ValueError("navigation path must remain relative")
        return value

    @model_validator(mode="after")
    def action_shape(self) -> Step:
        if self.action == "navigate":
            if self.url_path is None or self.target is not None:
                raise ValueError("navigate requires only a relative path")
        elif self.url_path is not None:
            raise ValueError("only navigation accepts a URL path")
        if self.action not in {"navigate", "assert_url", "screenshot"} and self.target is None:
            raise ValueError("action needs a stable locator")
        if (self.action in {"fill", "select"}) != (self.value_ref is not None):
            raise ValueError("fill/select need a symbolic value reference")
        assertion_kind = {
            "assert_visible": "visible",
            "assert_text": "text",
            "assert_url": "url",
            "assert_count": "count",
        }
        if self.action in assertion_kind and (
            self.assertion is None or self.assertion.kind != assertion_kind[self.action]
        ):
            raise ValueError("assertion shape does not match action")
        if self.action not in assertion_kind and self.assertion is not None:
            raise ValueError("unexpected assertion")
        return self


class Plan(Strict):
    schema_version: Literal["1.0"]
    goal: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    assumptions: list[str] = Field(max_length=15)
    steps: list[Step] = Field(min_length=1, max_length=15)
    success_criteria: list[str] = Field(min_length=1, max_length=15)
    warnings: list[str] = Field(max_length=15)

    @model_validator(mode="after")
    def required_observation(self) -> Plan:
        if self.schema_version != "1.0" or self.steps[0].action != "navigate":
            raise ValueError("invalid plan version or initial action")
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("step IDs must be unique")
        if not any(step.action.startswith("assert_") for step in self.steps):
            raise ValueError("plan must contain an assertion")
        return self


class Manifest(Strict):
    schema_version: str
    sandbox_id: str
    run_id: str
    origins: list[str] = Field(min_length=1, max_length=10)
    allowed_paths: list[str] = Field(min_length=1, max_length=20)
    allowed_methods: list[Literal["GET", "HEAD", "POST"]] = Field(min_length=1, max_length=3)
    max_pages: int = Field(ge=1, le=50)
    max_redirects: int = Field(ge=0, le=5)
    max_actions: int = Field(ge=1, le=50)
    max_duration_seconds: int = Field(ge=1, le=600)
    max_artifact_bytes: int = Field(ge=1, le=268435456)
    plan: Plan
