import io
import logging

import pytest

from app.logging_config import (
    SanitizingFormatter,
    configure_logging,
    report_async_error,
    report_unhandled,
    sanitize,
)


def test_flet_debug_payload_is_not_logged() -> None:
    record = logging.LogRecord(
        "flet.messaging",
        logging.DEBUG,
        __file__,
        1,
        "session %s contains private user content",
        ("session-value",),
        None,
    )
    output = SanitizingFormatter().format(record)
    assert "payload omitted" in output
    assert "session-value" not in output
    assert "private user content" not in output
    assert record.args == ("session-value",)


def test_exception_hooks_keep_tracebacks(caplog: pytest.LogCaptureFixture) -> None:
    import asyncio

    async def scenario() -> None:
        try:
            raise RuntimeError("intentional async failure")
        except RuntimeError as error:
            report_unhandled(type(error), error, error.__traceback__)
            report_async_error(asyncio.get_running_loop(), {"exception": error})

    asyncio.run(scenario())
    assert "Unhandled Python exception" in caplog.text
    assert "Unhandled asynchronous operation failed" in caplog.text
    assert caplog.text.count("Traceback") == 2


def test_logging_levels_and_traceback() -> None:
    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    try:
        configure_logging(True)
        assert logging.getLogger("flet").level == logging.WARNING
        assert logging.getLogger("flet_web").level == logging.WARNING
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(SanitizingFormatter("%(levelname)s %(name)s %(message)s"))
        root.handlers = [handler]
        logger = logging.getLogger("app.test")
        logger.debug("debug probe")
        try:
            raise ValueError("intentional diagnostic failure token=private-value")
        except ValueError:
            logger.exception("service=example operation=fetch attempt=1 failed")
        text = output.getvalue()
        assert "DEBUG app.test debug probe" in text
        assert "Traceback (most recent call last)" in text
        assert "ValueError: intentional diagnostic failure" in text
        assert "private-value" not in text
        configure_logging(False)
        assert not logger.isEnabledFor(logging.DEBUG)
        assert logging.getLogger("flet").level == logging.WARNING
    finally:
        root.handlers, root.level = previous_handlers, previous_level


def test_secret_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_ACCESS_TOKEN", "known-sensitive-value")
    text = sanitize(
        "known-sensitive-value password=hidden Bearer abc123 "
        "https://user:pass@example.com/api?token=query-secret#private"
    )
    for secret in ("known-sensitive-value", "hidden", "abc123", "user:pass", "query-secret"):
        assert secret not in text
    assert "https://example.com/api" in text
