"""Compact structured events with monotonic sequencing and conservative redaction."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any


class EventEmitter:
    def __init__(self) -> None:
        self.sequence = 0
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        self.sequence += 1
        safe = {
            k: v
            for k, v in fields.items()
            if not any(
                x in k.lower()
                for x in ("secret", "password", "token", "header", "credential", "body")
            )
        }
        item = {
            "event": event,
            "sequence": self.sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            **safe,
        }
        self.events.append(item)
        print(json.dumps(item, separators=(",", ":")), file=sys.stdout, flush=True)
        return item


emitter = EventEmitter()


def emit(event: str, **fields: Any) -> dict[str, Any]:
    return emitter.emit(event, **fields)
