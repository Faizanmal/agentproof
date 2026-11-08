from __future__ import annotations

from agentproof_api.api_models import RunReport
from agentproof_api.routes import _markdown_report, _playwright_export


def test_markdown_report_escapes_untrusted_markup_and_table_delimiters() -> None:
    report = {
        "run": {
            "id": "run-id",
            "created_at": "2026-11-07T00:00:00Z",
            "status": "completed",
            "verdict": "FAIL",
            "model": None,
            "policy_digest": None,
            "goal": "<script>alert(1)</script> | goal",
        },
        "steps": [],
        "findings": [],
        "artifacts": [],
    }
    markdown = _markdown_report(report)
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\\| goal" in markdown


def test_versioned_report_schema_accepts_complete_finding_record() -> None:
    record = {
        "id": "00000000-0000-4000-8000-000000000002",
        "created_at": "2026-11-07T00:00:00Z",
        "updated_at": "2026-11-07T00:00:00Z",
        "run_id": "00000000-0000-4000-8000-000000000001",
        "fingerprint": "a" * 64,
        "title": "Observed mismatch",
        "severity": "medium",
        "confidence": 0.7,
        "expected_json": {"expected": "Ready"},
        "observed_json": {"actual": "Not ready"},
        "repro_steps_json": ["Navigate to /"],
        "verdict": "FAIL",
        "status": "OPEN",
        "classification": "LIKELY",
        "environment_json": {"browser": "Chromium"},
        "hypotheses_json": [],
        "evidence_artifact_ids": [],
        "first_seen_at": "2026-11-07T00:00:00Z",
        "occurrence_count": 1,
    }
    report = RunReport.model_validate(
        {
            "schema_version": "agentproof.run-report/1.0",
            "run": {
                "id": "00000000-0000-4000-8000-000000000001",
                "created_at": "2026-11-07T00:00:00Z",
                "status": "completed",
                "verdict": "FAIL",
                "goal": "Verify the dashboard",
                "plan_version": 1,
                "model": "fixture-model",
                "policy_digest": "b" * 64,
            },
            "evidence_is_execution_only": True,
            "steps": [],
            "findings": [record],
            "artifacts": [],
            "observations": {},
        }
    )
    assert report.findings[0].classification == "LIKELY"


def test_playwright_export_uses_only_observed_actions_and_symbolic_credentials() -> None:
    plan = {
        "plan_json": {
            "schema_version": "1.0",
            "goal": "Check login",
            "assumptions": [],
            "risk_level": "LOW",
            "steps": [
                {
                    "id": "s1",
                    "action": "navigate",
                    "url_path": "/login",
                    "target": None,
                    "value_ref": None,
                    "assertion": None,
                    "requires_approval": False,
                },
                {
                    "id": "s2",
                    "action": "fill",
                    "url_path": None,
                    "target": {
                        "strategy": "role",
                        "role": "textbox",
                        "name": "Password",
                    },
                    "value_ref": "credential:password",
                    "assertion": None,
                    "requires_approval": False,
                },
                {
                    "id": "s3",
                    "action": "assert_text",
                    "url_path": None,
                    "target": {"strategy": "test_id", "name": "status"},
                    "value_ref": None,
                    "assertion": {"kind": "text", "expected": "Ready"},
                    "requires_approval": False,
                },
            ],
            "success_criteria": ["Ready is shown"],
            "warnings": [],
        }
    }
    report = {
        "run": {
            "id": "00000000-0000-4000-8000-000000000001",
            "created_at": "2026-11-07T00:00:00Z",
            "verdict": "FAIL",
            "model": "fixture-model",
            "policy_digest": "digest",
        },
        "steps": [
            {
                "ordinal": ordinal,
                "status": "completed",
                "observed": {"assertion": {"status": "PASS"}},
            }
            for ordinal in range(3)
        ],
        "artifacts": [
            {
                "id": "artifact-id",
                "kind": "trace",
                "sha256": "abc123",
                "expires_at": "2026-11-14T00:00:00Z",
            }
        ],
    }
    spec, readme = _playwright_export(plan, report)
    assert "process.env.AGENTPROOF_TEST_PASSWORD" in spec
    assert 'toHaveText("Ready")' in spec
    assert "TODO_MANUAL" not in spec
    assert "fixture password value" not in spec
    assert "/api/v1/artifacts/artifact-id/download" in readme
    assert "require a signed-in session" in readme
