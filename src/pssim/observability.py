"""Logovanie. Jediný povolený spôsob výpisu v aplikácii — `print()` je zakázaný.

`io/` loguje z iného vlákna než `viz/`, preto je konfigurácia štruktúrovaná
a obsahuje názov vlákna. Bez toho sa vláknové chyby nedajú čítať.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

_is_configured = False


def configure(level: str | None = None, *, json_output: bool = False) -> None:
    """Nastaví logovanie. Volaj raz, na začiatku v `cli.py`.

    Úroveň sa berie z argumentu, inak z `PSSIM_LOG_LEVEL`, inak `info`.
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
            # Názov vlákna je tu zámerne: io/ loguje z iného vlákna než viz/
            # a bez tohto sa vláknové chyby nedajú čítať.
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
    """Vráti logger pre modul. Použi `get_logger(__name__)`."""
    if not _is_configured:
        configure()
    return structlog.get_logger(name)
