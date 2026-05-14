from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import sys
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TypeVar, cast

try:
    from typing import ParamSpec
except ImportError:
    from typing_extensions import ParamSpec

TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="-")
COMPANY: ContextVar[str] = ContextVar("company", default="-")
STEP: ContextVar[str] = ContextVar("step", default="-")

P = ParamSpec("P")
R = TypeVar("R")


class ContextFilter(logging.Filter):
    """Inject request-scoped context into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = TRACE_ID.get()
        if not hasattr(record, "company"):
            record.company = COMPANY.get()
        if not hasattr(record, "step"):
            record.step = STEP.get()
        return True


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        if sys.stderr.isatty():
            original_levelname = record.levelname
            color = self.COLORS.get(record.levelno, "")
            record.levelname = f"{color}{record.levelname}{self.RESET}"
            formatted = super().format(record)
            record.levelname = original_levelname
            return formatted
        return super().format(record)


LOG_FORMAT = (
    "[%(asctime)s] [%(levelname)s] "
    "[trace_id=%(trace_id)s] [company=%(company)s] [step=%(step)s] "
    "%(name)s: %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    level: str = "INFO",
    log_file: str | Path = "logs/app.log",
    max_bytes: int = 5_000_000,
    backup_count: int = 5,
    enable_color: bool = True,
) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    context_filter = ContextFilter()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.addFilter(context_filter)
    console_formatter: logging.Formatter
    if enable_color:
        console_formatter = ColorFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    else:
        console_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    console_handler.setFormatter(console_formatter)

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    root.addHandler(console_handler)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextlib.contextmanager
def log_context(
    *,
    trace_id: str | None = None,
    company: str | None = None,
    step: str | None = None,
) -> Iterator[None]:
    trace_token = TRACE_ID.set(trace_id) if trace_id is not None else None
    company_token = COMPANY.set(company) if company is not None else None
    step_token = STEP.set(step) if step is not None else None
    try:
        yield
    finally:
        if step_token is not None:
            STEP.reset(step_token)
        if company_token is not None:
            COMPANY.reset(company_token)
        if trace_token is not None:
            TRACE_ID.reset(trace_token)


@contextlib.contextmanager
def log_step(logger: logging.Logger, step: str, message: str, **fields: Any) -> Iterator[None]:
    with log_context(step=step):
        start = time.perf_counter()
        logger.info("START %s %s", message, _format_fields(fields))
        try:
            yield
        except Exception:
            duration_ms = elapsed_ms(start)
            logger.exception("ERROR %s duration_ms=%s %s", message, duration_ms, _format_fields(fields))
            raise
        duration_ms = elapsed_ms(start)
        logger.info("END %s duration_ms=%s %s", message, duration_ms, _format_fields(fields))


def timed(
    step: str,
    message: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        label = message or func.__qualname__

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                logger = logging.getLogger(func.__module__)
                with log_context(step=step):
                    start = time.perf_counter()
                    logger.debug("START %s", label)
                    try:
                        result = await cast(Any, func)(*args, **kwargs)
                    except Exception:
                        logger.exception("ERROR %s duration_ms=%s", label, elapsed_ms(start))
                        raise
                    logger.debug("END %s duration_ms=%s", label, elapsed_ms(start))
                    return result

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            logger = logging.getLogger(func.__module__)
            with log_context(step=step):
                start = time.perf_counter()
                logger.debug("START %s", label)
                try:
                    result = func(*args, **kwargs)
                except Exception:
                    logger.exception("ERROR %s duration_ms=%s", label, elapsed_ms(start))
                    raise
                logger.debug("END %s duration_ms=%s", label, elapsed_ms(start))
                return result

        return sync_wrapper

    return decorator


def elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def _format_fields(fields: dict[str, Any]) -> str:
    if not fields:
        return ""
    safe_fields = {key: _redact(value) for key, value in fields.items()}
    return " ".join(f"{key}={value}" for key, value in safe_fields.items())


def _redact(value: Any) -> Any:
    if value is None:
        return "-"
    text = str(value)
    if "AIza" in text or "api_key" in text.lower():
        return "[REDACTED]"
    return text.replace("\n", " ")[:500]
