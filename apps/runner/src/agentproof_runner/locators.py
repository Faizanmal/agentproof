"""Stable, unique locator resolution. CSS is intentionally a tiny allowlist."""

from __future__ import annotations

import re

from playwright.sync_api import Locator, Page

from .contracts import Target
from .policy import PolicyDenied

_CSS = re.compile(
    r"(?:body|main|h[1-3]|#[A-Za-z][A-Za-z0-9_-]{0,78}|\[data-testid=\"[A-Za-z0-9_-]{1,80}\"\])\Z"
)


def locate(page: Page, target: Target, *, allow_multiple: bool = False) -> Locator:
    match target.strategy:
        case "role":
            if not target.role:
                raise PolicyDenied("role locator missing role")
            loc = page.get_by_role(target.role, name=target.name, exact=True)
        case "label":
            loc = page.get_by_label(target.name, exact=True)
        case "test_id":
            loc = page.get_by_test_id(target.name)
        case "placeholder":
            loc = page.get_by_placeholder(target.name, exact=True)
        case "text":
            loc = page.get_by_text(target.name, exact=True)
        case "css":
            if not _CSS.fullmatch(target.name):
                raise PolicyDenied("unsafe CSS locator")
            loc = page.locator(target.name)
        case _:
            raise PolicyDenied("unsupported locator")
    count = loc.count()
    if target.nth is not None:
        if target.nth >= count:
            raise PolicyDenied("locator disambiguator outside match count")
        return loc.nth(target.nth)
    if not allow_multiple and count != 1:
        raise PolicyDenied("locator must match exactly one element", details={"match_count": count})
    return loc
