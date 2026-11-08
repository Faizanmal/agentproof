"""Private, bounded evidence storage and conservative content redaction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentproof_api import db
from agentproof_api.security import now
from agentproof_api.settings import Settings

POLICY_VERSION = "agentproof-evidence-redaction-v1"
MAX_ITEM_BYTES = 26_214_400
MAX_RUN_BYTES = 104_857_600
TRACE_ACTIONS = {
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
}
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?!\d{4}-\d{2}-\d{2})(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|authorization|cookie|session)"
    r"\s*[:=]\s*([^\s,;<>]+)"
)
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_KEY = re.compile(r"authorization|cookie|password|passwd|token|secret|api[_-]?key", re.I)


def _safe_url(match: re.Match[str]) -> str:
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(match.group(0))
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = host + (f":{parts.port}" if parts.port else "")
        segments = parts.path.split("/")[:100]
        for index, segment in enumerate(segments):
            if _EMAIL.fullmatch(segment):
                segments[index] = "[REDACTED_EMAIL]"
            elif _PHONE.fullmatch(segment):
                segments[index] = "[REDACTED_PHONE]"
            elif re.fullmatch(
                r"(?i)(?:password|passwd|token|secret|api[_-]?key|authorization|session|reset|email|phone)",
                segment,
            ) and index + 1 < len(segments):
                segments[index + 1] = "[REDACTED]"
        safe_path = "/".join(segments)[:500]
        return urlunsplit(
            (parts.scheme, netloc, safe_path, "[REDACTED]" if parts.query else "", "")
        )
    except ValueError:
        return "[INVALID_URL]"


def redact_text(value: str) -> tuple[str, dict[str, int]]:
    counters = {"secret_assignments": 0, "emails": 0, "phone_numbers": 0, "urls": 0}

    def assignment(match: re.Match[str]) -> str:
        counters["secret_assignments"] += 1
        return f"{match.group(1)}=[REDACTED]"

    value = _ASSIGNMENT.sub(assignment, value[:120_000])
    value, counters["emails"] = _EMAIL.subn("[REDACTED_EMAIL]", value)
    value, counters["phone_numbers"] = _PHONE.subn("[REDACTED_PHONE]", value)

    def url(match: re.Match[str]) -> str:
        counters["urls"] += 1
        return _safe_url(match)

    return _URL.sub(url, value), counters


def redact_tree(value: Any, counts: dict[str, int] | None = None) -> Any:
    counts = counts if counts is not None else {}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                counts["sensitive_fields"] = counts.get("sensitive_fields", 0) + 1
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact_tree(item, counts)
        return result
    if isinstance(value, list):
        return [redact_tree(item, counts) for item in value[:10_000]]
    if isinstance(value, str):
        sanitized, local = redact_text(value)
        for key, count in local.items():
            counts[key] = counts.get(key, 0) + count
        return sanitized
    if value is None or type(value) in {bool, int, float, Decimal}:
        return value
    if isinstance(value, (datetime, date, UUID)):
        return value
    return str(value)[:2000]


def sanitize_artifact(filename: str, content: bytes) -> tuple[str, str, bytes, dict[str, int]]:
    if len(content) > MAX_ITEM_BYTES:
        raise HTTPException(413, "ARTIFACT_TOO_LARGE")
    suffix = Path(filename).suffix.lower()
    if suffix == ".json" and content.startswith((b"{", b"[")):
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(415, "INVALID_JSON_ARTIFACT") from None
        _validate_runner_trace(value)
        counts: dict[str, int] = {}
        safe = redact_tree(value, counts)
        return "trace", "application/json", json.dumps(safe, separators=(",", ":")).encode(), counts
    if suffix == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "screenshot", "image/png", content, {"masked_inputs": 1}
    if (
        suffix == ".webp"
        and len(content) > 12
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
    ):
        return "screenshot", "image/webp", content, {"masked_inputs": 1}
    raise HTTPException(415, "ARTIFACT_MIME_REJECTED")


def _validate_runner_trace(value: Any) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != "agentproof.run-trace/1.0":
        raise HTTPException(415, "INVALID_RUN_TRACE")
    if not isinstance(value.get("run_id"), str):
        raise HTTPException(415, "INVALID_RUN_TRACE")
    records = value.get("steps", [])
    observations = value.get("observations", {})
    if not isinstance(records, list) or len(records) > 50 or not isinstance(observations, dict):
        raise HTTPException(415, "INVALID_RUN_TRACE")
    for index, record in enumerate(records, 1):
        if (
            not isinstance(record, dict)
            or record.get("sequence") != index
            or not isinstance(record.get("step_id"), str)
            or record.get("action") not in TRACE_ACTIONS
            or record.get("status") not in {"PASS", "FAIL", "ERROR"}
            or not isinstance(record.get("observation", {}), dict)
        ):
            raise HTTPException(415, "INVALID_RUN_TRACE")
        assertion = record.get("assertion")
        if assertion is not None and (
            not isinstance(assertion, dict)
            or assertion.get("status") not in {"PASS", "FAIL", "ERROR", "INCONCLUSIVE"}
        ):
            raise HTTPException(415, "INVALID_RUN_TRACE")


def validate_artifact_filename(filename: str) -> None:
    if (
        Path(filename).name != filename
        or re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(?:png|webp|json)", filename) is None
    ):
        raise HTTPException(400, "ARTIFACT_NAME_REJECTED")


async def store_artifact(
    session: AsyncSession,
    config: Settings,
    run_id: UUID,
    filename: str,
    content: bytes,
    *,
    step_id: UUID | None = None,
) -> tuple[UUID, bool] | None:
    kind, mime_type, safe_content, counts = sanitize_artifact(filename, content)
    source_hash = hashlib.sha256(content).hexdigest()
    result_hash = hashlib.sha256(safe_content).hexdigest()
    validate_artifact_filename(filename)
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem
    safe_step = str(step_id) if step_id else "run"
    private_name = f"{run_id}/{stem}_{safe_step}_{result_hash[:16]}{suffix}"
    existing = (
        await session.execute(
            select(db.artifacts.c.id).where(
                db.artifacts.c.run_id == run_id,
                db.artifacts.c.private_storage_key == private_name,
                db.artifacts.c.sha256 == result_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    existing_total = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(db.artifacts.c.bytes), 0)).where(
                    db.artifacts.c.run_id == run_id
                )
            )
        ).scalar_one()
    )
    if existing_total + len(safe_content) > MAX_RUN_BYTES:
        raise HTTPException(413, "RUN_ARTIFACT_BUDGET_EXCEEDED")
    root = config.artifact_storage_root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        root.chmod(0o700)
    target = root / private_name
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".evidence-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(safe_content)
    if os.name != "nt":
        await asyncio.to_thread(temporary.chmod, 0o600)
    await asyncio.to_thread(temporary.replace, target)
    artifact_id = uuid4()
    expires = now() + timedelta(days=7)
    try:
        await session.execute(
            insert(db.artifacts).values(
                id=artifact_id,
                run_id=run_id,
                step_id=step_id,
                kind=kind,
                private_storage_key=private_name,
                sha256=result_hash,
                mime_type=mime_type,
                bytes=len(safe_content),
                redaction_state="sanitized",
                expires_at=expires,
            )
        )
        await session.execute(
            insert(db.artifact_redaction_audits).values(
                run_id=run_id,
                artifact_id=artifact_id,
                source_sha256=source_hash,
                result_sha256=result_hash,
                redaction_policy=POLICY_VERSION,
                redactions_json=counts,
            )
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return artifact_id, True
