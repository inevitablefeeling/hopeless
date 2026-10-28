"""Logging setup. Plain text by default; set LOG_FORMAT=json for structured logs."""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", *, json_format: Optional[bool] = None) -> None:
    if json_format is None:
        json_format = os.getenv("LOG_FORMAT", "").strip().lower() == "json"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_format else logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # discord.py is very chatty at DEBUG; keep it at INFO or above.
    logging.getLogger("discord").setLevel(max(logging.getLevelName(level), logging.INFO))
