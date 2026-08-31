from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RequestLogger:
    """Write request events as JSON Lines grouped by idempotence key."""

    def __init__(self, log_path: str | PathLike[str] | None):
        self.log_path = Path(log_path) if log_path else None
        self._disabled = False

    def log(self, idempotence_key: str, event: str, payload: dict[str, Any]) -> None:
        if not self.log_path or self._disabled:
            return
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "idempotence_key": idempotence_key,
            "event": event,
            "payload": payload,
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            self._disabled = True
            logger.warning("Disabling request event logging after write failure: %s", exc)
