from __future__ import annotations

import logging

from utils.logging import SensitiveDataFilter


def _record(msg: str, args: tuple) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_sensitive_filter_keeps_numeric_arg_type_for_percent_format() -> None:
    record = _record("count=%d", (7,))
    assert SensitiveDataFilter().filter(record) is True
    assert record.getMessage() == "count=7"


def test_sensitive_filter_redacts_sensitive_string_args() -> None:
    record = _record("auth=%s", ("Bearer supersecrettoken",))
    assert SensitiveDataFilter().filter(record) is True
    message = record.getMessage()
    assert "Bearer [REDACTED]" in message
