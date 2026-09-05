from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any


logger = logging.getLogger("auri.turns")


class TurnLogger:
    """Append-only JSONL logger for per-turn agent activity (tool calls + timing)."""

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def log(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:  # noqa: BLE001 - logging must never break a reply
            logger.warning("failed to write turn log: %s", exc)
