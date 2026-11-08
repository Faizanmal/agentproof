"""Redaction helpers for traces and artifacts; never serialize auth headers."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_SENSITIVE = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
)
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
_SENSITIVE_PATH_PART = re.compile(
    r"(?i)^(?:password|passwd|token|secret|api[_-]?key|authorization|session|reset|email|phone)$"
)


def redact_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    result = _SENSITIVE.sub(r"\1=[REDACTED]", value[:12000])
    result = _EMAIL.sub("[REDACTED_EMAIL]", result)
    result = _PHONE.sub("[REDACTED_PHONE]", result)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = host + (f":{parts.port}" if parts.port is not None else "")
        segments = parts.path.split("/")[:100]
        for index, segment in enumerate(segments):
            if _EMAIL.fullmatch(segment):
                segments[index] = "[REDACTED_EMAIL]"
            elif _SENSITIVE_PATH_PART.fullmatch(segment) and index + 1 < len(segments):
                segments[index + 1] = "[REDACTED]"
            elif _PHONE.fullmatch(segment):
                segments[index] = "[REDACTED_PHONE]"
        safe_path = "/".join(segments)[:500]
        return urlunsplit(
            (parts.scheme, netloc, safe_path, "[REDACTED]" if parts.query else "", "")
        )
    except ValueError:
        return "[INVALID_URL]"
