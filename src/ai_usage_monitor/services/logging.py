from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

cycle_id: ContextVar[str | None] = ContextVar("cycle_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    # HTTP transport diagnostics can contain server headers/cookies. The monitor
    # emits its own sanitized events, never library request/response traces.
    for name in (
        "httpx",
        "httpcore",
        "httpx2",
        "httpcore2",
        "mcp.server.transport_security",
    ):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def log_event(
    logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any
) -> None:
    if cycle_id.get() is not None:
        fields["cycle_id"] = cycle_id.get()
    logger.log(level, event, extra={"event_fields": fields})
