import pytest
from agentproof_api.api_models import RunReport
from agentproof_api.evidence import (
    MAX_ITEM_BYTES,
    redact_tree,
    sanitize_artifact,
    validate_artifact_filename,
)
from fastapi import HTTPException


def test_nested_json_redacts_secret_keys_pii_and_query_values() -> None:
    counts: dict[str, int] = {}
    result = redact_tree(
        {
            "request": {
                "Authorization": "Bearer actual-token",
                "password": "raw-password",  # pragma: allowlist secret
            },
            "nested": [
                "Contact qa.user@example.test",
                "https://site.example/reset/private-token?email=qa.user@example.test&token=abc",
                "phone: +1 415 555 0100",
            ],
        },
        counts,
    )
    rendered = str(result)
    for raw in (
        "actual-token",
        "raw-password",
        "qa.user@example.test",
        "555 0100",
        "token=abc",
        "private-token",
    ):
        assert raw not in rendered
    assert result["request"]["Authorization"] == "[REDACTED]"
    assert counts["sensitive_fields"] == 2


def test_png_mime_and_redaction_policy_are_explicit() -> None:
    kind, mime, safe, audit = sanitize_artifact("screen.png", b"\x89PNG\r\n\x1a\nfixture")
    assert kind == "screenshot" and mime == "image/png"
    assert safe.startswith(b"\x89PNG\r\n\x1a\n")
    assert audit == {"masked_inputs": 1}


def test_mime_trick_invalid_trace_and_oversized_upload_are_rejected() -> None:
    with pytest.raises(HTTPException, match="ARTIFACT_MIME_REJECTED"):
        sanitize_artifact("shell.png", b"<script>alert(1)</script>")
    with pytest.raises(HTTPException, match="INVALID_JSON_ARTIFACT"):
        sanitize_artifact("trace.json", b"{broken")
    with pytest.raises(HTTPException, match="INVALID_RUN_TRACE"):
        sanitize_artifact("trace.json", b'{"schema_version":"1.0"}')
    with pytest.raises(HTTPException, match="ARTIFACT_TOO_LARGE"):
        sanitize_artifact("large.png", b"x" * (MAX_ITEM_BYTES + 1))


def test_report_schema_rejects_unrecognized_top_level_fields() -> None:
    with pytest.raises(ValueError):
        RunReport.model_validate(
            {
                "schema_version": "agentproof.run-report/1.0",
                "run": {},
                "evidence_is_execution_only": True,
                "steps": [],
                "findings": [],
                "artifacts": [],
                "observations": {},
                "model_claimed_pass": True,
            }
        )


@pytest.mark.parametrize("filename", ["../escape.png", "a\\escape.json", "C:evil.webp"])
def test_artifact_filename_rejects_traversal(filename: str) -> None:
    with pytest.raises(HTTPException, match="ARTIFACT_NAME_REJECTED"):
        validate_artifact_filename(filename)
