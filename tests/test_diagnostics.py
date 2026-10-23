import logging
from pathlib import Path

from orpheus.diagnostics import configure_logging, record_event


def test_rotating_log_is_bounded_and_rotates_in_temp_directory(tmp_path: Path):
    configure_logging(tmp_path)
    logger = logging.getLogger("orpheus.diagnostics")
    handler = next(handler for handler in logger.handlers
                   if getattr(handler, "_orpheus_diagnostics", False))
    assert handler.maxBytes == 1_048_576
    assert handler.backupCount == 3

    for _ in range(50_000):
        record_event("capture_started", backend="cpu")
    handler.flush()
    log_files = list(tmp_path.glob("orpheus.log*"))
    assert 1 < len(log_files) <= 4
    assert all(path.stat().st_size <= handler.maxBytes for path in log_files)


def test_only_allowlisted_diagnostic_fields_are_persisted(tmp_path: Path):
    marker = "UNIQUE_TRANSCRIPT_CREDENTIAL_MARKER"
    configure_logging(tmp_path)
    record_event("transcription_completed", backend="cpu", duration_ms=12.4,
                 error_category=marker, compute_type=marker)
    record_event(marker, backend=marker, duration_ms=marker)
    handler = logging.getLogger("orpheus.diagnostics").handlers[0]
    handler.flush()

    contents = (tmp_path / "orpheus.log").read_text(encoding="utf-8")
    assert "transcription_completed" in contents
    assert '"duration_ms":12' in contents
    assert marker not in contents


def test_reconfiguration_closes_old_file_handler(tmp_path: Path):
    configure_logging(tmp_path)
    logger = logging.getLogger("orpheus.diagnostics")
    old = next(handler for handler in logger.handlers
               if getattr(handler, "_orpheus_diagnostics", False))
    configure_logging(tmp_path)
    assert old.stream is None
    assert sum(bool(getattr(handler, "_orpheus_diagnostics", False))
               for handler in logger.handlers) == 1
