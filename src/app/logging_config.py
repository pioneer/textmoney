"""Central logging, defensive redaction, and unhandled exception reporting."""

import asyncio
import logging
import os
import re
import sys
import threading
from copy import copy
from types import TracebackType
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)
_SECRET_NAME = re.compile(
    r"password|passwd|token|secret|cookie|authorization|api[_-]?key|credential|access[_-]?key",
    re.IGNORECASE,
)
_LABELED_SECRET = re.compile(
    r"(?i)([\w-]*(?:password|passwd|token|secret|cookie|authorization|api[_-]?key|"
    r"credential|access[_-]?key)[\w-]*[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_URL = re.compile(r"https?://[^\s<>\"']+")


def sanitize(text: str) -> str:
    for name, value in os.environ.items():
        if value and len(value) >= 4 and _SECRET_NAME.search(name):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;\"']+", r"\1 [REDACTED]", text)
    text = re.sub(
        r"(?im)\b((?:set-)?cookie|authorization)\s*:\s*[^\r\n]+",
        r"\1: [REDACTED]",
        text,
    )

    def clean_url(match: re.Match[str]) -> str:
        try:
            parts = urlsplit(match.group())
            return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
        except ValueError:
            return "[REDACTED URL]"

    return _LABELED_SECRET.sub(r"\1[REDACTED]", _URL.sub(clean_url, text))


class SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if record.levelno < logging.INFO and (
            record.name == "flet" or record.name.startswith("flet.")
        ):
            record = copy(record)
            record.msg = "Diagnostic at %s.%s:%s (payload omitted)"
            record.args = (record.module, record.funcName, record.lineno)
        return sanitize(super().format(record))


def configure_logging(debug: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SanitizingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("app").setLevel(logging.DEBUG if debug else logging.INFO)
    logging.getLogger("flet").setLevel(logging.WARNING)
    logging.getLogger("flet_web").setLevel(logging.WARNING)
    logging.getLogger("flet_transport").setLevel(logging.WARNING)
    logging.getLogger("flet_object_patch").setLevel(logging.WARNING)
    for name in ("httpx", "httpcore", "urllib3", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logger.debug("Debug diagnostics enabled")


def report_unhandled(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: TracebackType | None,
) -> None:
    if issubclass(exception_type, KeyboardInterrupt):
        sys.__excepthook__(exception_type, exception, traceback)
        return
    logger.critical("Unhandled Python exception", exc_info=(exception_type, exception, traceback))


def report_thread_error(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is not None and args.exc_type is not SystemExit:
        report_unhandled(args.exc_type, args.exc_value, args.exc_traceback)


def report_async_error(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
    exception = context.get("exception")
    if isinstance(exception, BaseException):
        logger.error(
            "Unhandled asynchronous operation failed",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
    else:
        logger.error("Async runtime error: %s", context.get("message", "Unknown error"))


def install_exception_hooks() -> None:
    sys.excepthook = report_unhandled
    if sys.platform != "emscripten":
        threading.excepthook = report_thread_error
