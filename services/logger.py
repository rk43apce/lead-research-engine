import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional


LOG_FORMAT = "%(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str = "INFO", log_file: str = "logs/app.log") -> None:
    """Configure rotating file logs.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)


def log_info(
    message: str,
    company: Optional[str] = None,
    step: Optional[str] = None,
    **fields: Any,
) -> None:
    """Log a normal pipeline event."""
    logging.info(_build_message("INFO", message, company, step, fields), stacklevel=2)


def log_warning(
    message: str,
    company: Optional[str] = None,
    step: Optional[str] = None,
    **fields: Any,
) -> None:
    """Log a recoverable issue such as a skipped row, retry, or validation warning."""
    logging.warning(_build_message("WARNING", message, company, step, fields), stacklevel=2)


def log_error(
    message: str,
    company: Optional[str] = None,
    step: Optional[str] = None,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """Log an error.

    Use exc_info=True when debugging failures so stack traces go to console and logs/app.log.
    """
    logging.error(_build_message("ERROR", message, company, step, fields), exc_info=exc_info, stacklevel=2)


def log_debug(
    message: str,
    company: Optional[str] = None,
    step: Optional[str] = None,
    **fields: Any,
) -> None:
    """Log detailed debugging information.

    DEBUG is useful for AI workflows when you need previews or internal details without
    making normal demo logs noisy.
    """
    logging.debug(_build_message("DEBUG", message, company, step, fields), stacklevel=2)


def log_timing(start_time: float) -> int:
    """Return elapsed milliseconds for simple duration logging."""
    return int((time.perf_counter() - start_time) * 1000)


def _build_message(
    level: str,
    message: str,
    company: Optional[str],
    step: Optional[str],
    fields: dict[str, Any],
) -> str:
    log_data = {
        "timestamp": time.strftime(DATE_FORMAT),
        "level": level,
        "message": message,
    }

    if company:
        log_data["company"] = _safe_value(company)

    if step:
        log_data["step"] = _safe_value(step)

    for key, value in fields.items():
        log_data[key] = _safe_value(value)

    return json.dumps(log_data, ensure_ascii=True)


def _safe_value(value: Any) -> str:
    """Avoid leaking secrets or huge payloads into logs."""
    if value is None:
        return "-"

    text = str(value).replace("\n", " ")

    if "AIza" in text or "api_key" in text.lower() or "x-goog-api-key" in text.lower():
        return "[REDACTED]"

    return text[:500]
