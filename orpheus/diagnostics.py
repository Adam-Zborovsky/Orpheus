"""Privacy-limited rotating diagnostic logs.

The file logger is intentionally separate from Python's root logger. Only
fixed event categories and allowlisted scalar fields reach disk; arbitrary
third-party messages and exception text are never handled here.
"""
from __future__ import annotations

import json
import logging
import math
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOGGER_NAME = "orpheus.diagnostics"
_EVENTS = {
    "app_started", "capture_started", "capture_limit", "capture_stopped",
    "transcription_started", "transcription_completed", "cleanup_started",
    "cleanup_completed", "delivery_started", "delivery_completed",
    "delivery_failed", "history_failed", "model_loaded", "model_load_failed",
    "shutdown_started", "shutdown_completed", "settings_load_failed",
    "settings_save_failed",
}
_BACKENDS = {"cpu", "cuda", "auto"}
_COMPUTE_TYPES = {
    "auto", "default", "float32", "float16", "bfloat16", "int8",
    "int8_float32", "int8_float16", "int8_bfloat16", "int16",
}
_ERROR_CATEGORIES = {
    "io", "config", "audio", "model", "transcription", "cleanup",
    "delivery", "history", "cancelled", "unexpected",
}


def configure_logging(data_dir: Path) -> None:
    """Configure a dedicated 1 MiB × 4 rotating diagnostic log in data_dir."""
    log_path = Path(data_dir) / "orpheus.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in tuple(logger.handlers):
        if getattr(handler, "_orpheus_diagnostics", False):
            logger.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        log_path, maxBytes=1_048_576, backupCount=3, encoding="utf-8",
    )
    handler._orpheus_diagnostics = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)


def record_event(
    event: str,
    *,
    backend: str | None = None,
    compute_type: str | None = None,
    duration_ms: int | float | None = None,
    error_category: str | None = None,
) -> None:
    """Persist one allowlisted event; unknown values are omitted safely."""
    if not isinstance(event, str) or event not in _EVENTS:
        return
    payload: dict[str, Any] = {"event": event}
    if isinstance(backend, str) and backend in _BACKENDS:
        payload["backend"] = backend
    if isinstance(compute_type, str) and compute_type in _COMPUTE_TYPES:
        payload["compute_type"] = compute_type
    if (isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool)
            and math.isfinite(duration_ms) and duration_ms >= 0):
        payload["duration_ms"] = round(duration_ms)
    if isinstance(error_category, str) and error_category in _ERROR_CATEGORIES:
        payload["error_category"] = error_category
    logging.getLogger(_LOGGER_NAME).info(
        "%s", json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )
