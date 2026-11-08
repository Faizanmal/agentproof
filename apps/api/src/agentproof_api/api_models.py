from datetime import datetime
from typing import Annotated, Any, Literal, Self, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)

from agentproof_api.plan_schema import QAPlanV1, TestScope


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def normalize_email(value: str) -> str:
    from email_validator import EmailNotValidError, validate_email

    try:
        return str(
            validate_email(value.strip(), check_deliverability=False).normalized
        ).casefold()
    except EmailNotValidError:
        raise ValueError("Invalid email") from None


class Credentials(Model):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def email_normalized(cls, value: str) -> str:
        return normalize_email(value)


class LoginInput(Credentials):
    password: SecretStr = Field(min_length=1, max_length=256)


class EmailInput(Model):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def email_normalized(cls, value: str) -> str:
        return normalize_email(value)


class TokenInput(Model):
    token: SecretStr = Field(min_length=32, max_length=256)


class PasswordResetInput(Model):
    token: SecretStr = Field(min_length=32, max_length=256)
    password: SecretStr = Field(min_length=12, max_length=256)


class UserView(Model):
    id: UUID
    email: str
    email_verified_at: datetime | None


class Identity(Model):
    user: UserView
    csrf_token: str


class WorkspaceInput(Model):
    name: str = Field(min_length=1, max_length=120)


class MemberInput(Model):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["admin", "member", "viewer"]


class ProjectInput(WorkspaceInput):
    workspace_id: UUID
    base_url: str = Field(max_length=2048)

    @field_validator("base_url")
    @classmethod
    def valid_base(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
        ):
            raise ValueError("HTTP origin required without credentials or fragment")
        _ = parts.port
        return value.rstrip("/")


class ProjectPatch(Model):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "archived"] | None = None


class Navigate(Model):
    action: Literal["navigate"]
    path: str = Field(min_length=1, max_length=2048)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or chr(92) in value:
            raise ValueError("A relative path is required")
        return value


class Click(Model):
    action: Literal["click"]
    selector: str = Field(min_length=1, max_length=500)


class AssertText(Model):
    action: Literal["assert_text"]
    selector: str = Field(min_length=1, max_length=500)
    expected: str = Field(min_length=1, max_length=2000)


class Screenshot(Model):
    action: Literal["screenshot"]
    selector: str = Field(min_length=1, max_length=500)


Action = Annotated[Navigate | Click | AssertText | Screenshot, Field(discriminator="action")]


class PlanInput(Model):
    goal: str = Field(min_length=1, max_length=4000)
    steps: list[Action] | None = Field(default=None, min_length=5, max_length=15)


class RunInput(Model):
    goal: str | None = Field(default=None, min_length=1, max_length=4000)
    plan_id: UUID | None = None
    scope: TestScope = Field(default_factory=TestScope)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    max_duration_seconds: int = Field(default=600, ge=1, le=600)

    @model_validator(mode="after")
    def require_goal_or_plan(self) -> Self:
        if (self.goal is None) == (self.plan_id is None):
            raise ValueError("Provide exactly one of goal or plan_id")
        return self


class Record(Model):
    id: UUID
    created_at: datetime
    updated_at: datetime


class WorkspaceView(Record):
    name: str
    owner_user_id: UUID


class MemberView(Record):
    workspace_id: UUID
    user_id: UUID
    role: Literal["owner", "admin", "member", "viewer"]


class ProjectView(Record):
    workspace_id: UUID
    name: str
    base_url: str
    status: Literal["active", "archived"]
    created_by: UUID


class PlanView(Record):
    project_id: UUID
    created_by: UUID
    version: int
    goal: str
    scope_json: dict[str, Any]
    authorization_digest: str | None
    rejection_code: str | None
    plan_json: QAPlanV1 | dict[str, Any] | None
    risk_level: Literal["low", "medium", "high"]
    status: Literal["draft", "approved", "rejected"]
    approved_by: UUID | None
    approved_at: datetime | None


class RunView(Record):
    run_id: UUID | None = None
    project_id: UUID
    plan_id: UUID
    created_by: UUID
    idempotency_key: str
    status: Literal[
        "queued",
        "planning",
        "awaiting_approval",
        "running",
        "investigating",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
    ]
    verdict: Literal["PASS", "FAIL", "ERROR", "INCONCLUSIVE"] | None
    error_code: str | None
    limits_json: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    summary_json: dict[str, Any]
    events_url: str | None = None


class StepView(Record):
    run_id: UUID
    ordinal: int
    typed_action_json: dict[str, Any]
    expected_json: dict[str, Any]
    observed_json: dict[str, Any]
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    error_code: str | None


class ArtifactView(Record):
    run_id: UUID
    step_id: UUID | None
    kind: str
    sha256: str
    mime_type: str
    bytes: int
    redaction_state: str
    expires_at: datetime


class FindingView(Record):
    run_id: UUID
    fingerprint: str
    title: str
    severity: str
    confidence: float
    expected_json: dict[str, Any]
    observed_json: dict[str, Any]
    repro_steps_json: list[str]
    verdict: str
    status: str
    classification: str
    environment_json: dict[str, Any]
    hypotheses_json: list[str]
    evidence_artifact_ids: list[UUID]
    first_seen_at: datetime
    occurrence_count: int


class ReportRun(Model):
    id: UUID
    created_at: datetime
    status: str
    verdict: str | None
    goal: str
    plan_version: int
    model: str | None
    policy_digest: str | None


class ReportStep(Model):
    id: UUID
    ordinal: int
    action: dict[str, Any]
    expected: dict[str, Any]
    observed: dict[str, Any]
    page_observation: dict[str, Any]
    status: str
    started_at: datetime | None
    ended_at: datetime | None


class ReportFinding(Record):
    run_id: UUID
    fingerprint: str
    title: str
    severity: str
    confidence: float
    expected_json: dict[str, Any]
    observed_json: dict[str, Any]
    repro_steps_json: list[str]
    verdict: str
    status: str
    classification: str
    environment_json: dict[str, Any]
    hypotheses_json: list[str]
    evidence_artifact_ids: list[UUID]
    first_seen_at: datetime
    occurrence_count: int


class ReportArtifact(Model):
    id: UUID
    kind: str
    mime_type: str
    bytes: int
    sha256: str
    redaction_state: str
    expires_at: datetime
    download_url: str


class RunReport(Model):
    schema_version: Literal["agentproof.run-report/1.0"]
    run: ReportRun
    evidence_is_execution_only: Literal[True]
    steps: list[ReportStep]
    findings: list[ReportFinding]
    artifacts: list[ReportArtifact]
    observations: dict[str, Any]


class AuditView(Record):
    workspace_id: UUID
    actor_id: UUID
    action: str
    resource_type: str
    resource_id: UUID
    details_redacted: dict[str, Any]


T = TypeVar("T")


class Page[T](Model):
    data: list[T]
    next_cursor: str | None = None


class OriginInput(Model):
    method: Literal["https-file", "dns-txt", "demo"] = "https-file"
    origin: str = Field(min_length=8, max_length=2048)


class OriginView(Record):
    project_id: UUID
    normalized_origin: str
    method: Literal["http-file", "https-file", "dns-txt", "demo"]
    verified_at: datetime | None
    expires_at: datetime
    policy_json: dict[str, Any]


class OriginChallenge(OriginView):
    challenge: str
    instructions: str = "Publish the exact token at the specified proof location."


class VerifyTargetInput(Model):
    origin_id: UUID
    attestation: bool = False


class SecretInput(Model):
    confirmed_test_account: Literal[True]
    label: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9-]{0,78}$")
    username: SecretStr = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=256)


class SecretView(Model):
    id: UUID
    label: str
    created_at: datetime


class Message(Model):
    message: str


class PasswordResetRequested(Model):
    message: str
    development_reset_url: str | None = None


class VerificationMailSent(Model):
    message: str
    development_verification_url: str | None = None


class DevelopmentMail(Model):
    purpose: Literal["verification", "password_reset"]
    url: str
    expires_at: datetime

    @computed_field
    def verification_url(self) -> str | None:
        return self.url if self.purpose == "verification" else None

    @computed_field
    def password_reset_url(self) -> str | None:
        return self.url if self.purpose == "password_reset" else None


class CsrfView(Model):
    csrf_token: str
