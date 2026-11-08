"""Per-request and user-action policy enforcement (defense in depth only)."""

from __future__ import annotations

import ipaddress
import socket
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlsplit

from .contracts import Manifest
from .event_emitter import emit


class PolicyDenied(Exception):
    def __init__(self, reason: str, *, details: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def host_allowed(url: str, origins: list[str]) -> bool:
    try:
        p = urlsplit(url)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
            return False
        if f"{p.scheme}://{p.netloc.lower()}" not in origins:
            return False
        port = p.port or (443 if p.scheme == "https" else 80)
        answers = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
        return bool(answers) and all(
            ipaddress.ip_address(str(a[4][0]).split("%", 1)[0]).is_global for a in answers
        )
    except (ValueError, OSError):
        return False


def path_allowed(url: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(urlsplit(url).path or "/", pat) for pat in patterns)


def guard_action(page: Any, locator: Any, action: str, manifest: Manifest) -> None:
    label = (
        " ".join(
            filter(
                None,
                [
                    locator.get_attribute("aria-label"),
                    locator.inner_text(timeout=1000) if action == "click" else "",
                    locator.get_attribute("name"),
                ],
            )
        )
        .strip()
        .lower()[:300]
    )
    forbidden = (
        "logout",
        "log out",
        "delete",
        "purchase",
        "buy now",
        "pay",
        "payout",
        "send",
        "invite",
        "reset password",
        "export",
    )
    if action == "click" and any(word in label for word in forbidden):
        emit("sandbox.policy_denied", reason="SENSITIVE_CONTROL_BLOCKED", step_action=action)
        raise PolicyDenied("sensitive control blocked")
    if action == "click" and locator.get_attribute("type") == "submit":
        form = locator.locator("xpath=ancestor::form[1]")
        form_action = locator.get_attribute("formaction") or form.get_attribute("action")
        if form_action:
            effective = (
                urlsplit(page.url)._replace(path=form_action).geturl()
                if form_action.startswith("/")
                else form_action
            )
            if not host_allowed(effective, manifest.origins) or not path_allowed(
                effective, manifest.allowed_paths
            ):
                emit("sandbox.policy_denied", reason="FORM_TARGET_BLOCKED")
                raise PolicyDenied("form target outside authorized scope")


def install_request_guards(context: Any, manifest: Manifest, *, cancelled: Any = None) -> None:
    counters = {"pages": 0, "redirects": 0}

    def route_handler(route: Any) -> None:
        req = route.request
        reason = None
        if cancelled is not None and cancelled.is_set():
            reason = "RUN_CANCELLED"
        elif req.method.upper() not in manifest.allowed_methods:
            reason = "METHOD_BLOCKED"
        elif req.resource_type == "document":
            if req.redirected_from:
                counters["redirects"] += 1
                if counters["redirects"] > manifest.max_redirects:
                    reason = "REDIRECT_LIMIT_EXCEEDED"
            else:
                counters["pages"] += 1
                if counters["pages"] > manifest.max_pages:
                    reason = "PAGE_LIMIT_EXCEEDED"
        if not reason and (
            not host_allowed(req.url, manifest.origins)
            or not path_allowed(req.url, manifest.allowed_paths)
        ):
            reason = "ORIGIN_OR_PATH_BLOCKED"
        if reason:
            emit("sandbox.policy_denied", reason=reason, url_path=urlsplit(req.url).path[:200])
            route.abort("blockedbyclient")
        else:
            route.continue_()

    context.route("**/*", route_handler)

    def ws(route: Any) -> None:
        if not host_allowed(route.url, manifest.origins) or not path_allowed(
            route.url, manifest.allowed_paths
        ):
            emit("sandbox.policy_denied", reason="WEBSOCKET_ORIGIN_BLOCKED")
            route.close(code=1008, reason="policy denied")
        else:
            route.connect_to_server()

    context.route_web_socket("**/*", ws)
