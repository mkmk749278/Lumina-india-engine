"""Shared utilities. Logging is centralised here.

Always obtain loggers via :func:`get_logger` — never ``print`` or the stdlib
``logging`` module (CLAUDE.md). ``diagnose`` is disabled so tracebacks never
expand local variables, which keeps broker tokens/secrets out of logs.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:  # `Logger` lives only in loguru's type stub, not at runtime.
    from loguru import Logger

_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        backtrace=False,
        diagnose=False,
    )
    _configured = True


def get_logger(name: str) -> Logger:
    """Return a module-bound logger."""
    _configure()
    return logger.bind(module=name)
