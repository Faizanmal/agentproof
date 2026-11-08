"""Stable, unique locator resolution. CSS is intentionally a tiny allowlist."""

from __future__ import annotations

import re

from playwright.sync_api import Locator, Page

from .contracts import Target
from .policy import PolicyDenied

_CSS = re.compile(
    r"(?:body|main|h[1-3]|#[A-Za-z][A-Za-z0-9_-]{0,78}|\[data-testid=\"[A-Za-z0-9_-]{1,80}\"\])\Z"
)


def _raw_locator(page: Page, target: Target) -> Locator:
    match target.strategy:
        case "role":
            if not target.role:
                raise PolicyDenied("role locator missing role")
            return page.get_by_role(target.role, name=target.name, exact=True)
        case "label":
            return page.get_by_label(target.name, exact=True)
        case "test_id":
            return page.get_by_test_id(target.name)
        case "placeholder":
            return page.get_by_placeholder(target.name, exact=True)
        case "text":
            return page.get_by_text(target.name, exact=True)
        case "css":
            if not _CSS.fullmatch(target.name):
                raise PolicyDenied("unsafe CSS locator")
            return page.locator(target.name)
        case _:
            raise PolicyDenied("unsupported locator")


def resolve_locator(page: Page, target: Target) -> tuple[Locator, int]:
    """Resolve without denying on zero matches; callers decide FAIL vs ERROR."""
    loc = _raw_locator(page, target)
    count = loc.count()
    if target.nth is not None:
        if target.nth >= count:
            return loc, 0
        return loc.nth(target.nth), 1
    return loc, count


def locate(page: Page, target: Target, *, allow_multiple: bool = False) -> Locator:
    loc, count = resolve_locator(page, target)
    if target.nth is not None:
        if count == 0:
            raise PolicyDenied("locator disambiguator outside match count")
        return loc
    if not allow_multiple and count != 1:
        raise PolicyDenied("locator must match exactly one element", details={"match_count": count})
    return loc
