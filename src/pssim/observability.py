"""Logging. The only permitted way of producing output in the application — `print()` is forbidden.

`io/` logs from a different thread than `viz/`, which is why the configuration is
structured and carries the thread name. Without it, threading bugs are unreadable.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

_is_configured = False


def configure(level: str | None = None, *, json_output: bool = False) -> None:
    """Set up logging. Call it once, at the start, in `cli.py`.

    The level comes from the argument, otherwise from `PSSIM_LOG_LEVEL`, otherwise
    `info`.
    """
    global _is_configured

    resolved = (level or os.environ.get("PSSIM_LOG_LEVEL") or "info").upper()
    numeric_level = getattr(logging, resolved, logging.INFO)

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S.%f", utc=False),
            # The thread name is here deliberately: io/ logs from a different
            # thread than viz/, and without it threading bugs are unreadable.
            structlog.processors.CallsiteParameterAdder(
                [structlog.processors.CallsiteParameter.THREAD_NAME]
            ),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _is_configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return the logger for a module. Use `get_logger(__name__)`."""
    if not _is_configured:
        configure()
    return structlog.get_logger(name)
