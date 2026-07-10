"""Shared utilities. Logging is centralised here.

Always obtain loggers via :func:`get_logger` — never ``print`` or the stdlib
``logging`` module (CLAUDE.md). ``diagnose`` is disabled so tracebacks never
expand local variables, which keeps broker tokens/secrets out of logs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
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
    # Optional rotating file sink on the data volume (set INDIA_LOG_DIR;
    # compose points it at /app/data/logs). Docker's json-file logs cap at
    # ~30 MB — a slow-burn incident can outlive them before it's
    # investigated; this keeps two weeks of forensic history. Best-effort:
    # an unwritable dir must never take the engine down.
    log_dir = os.environ.get("INDIA_LOG_DIR", "")
    if log_dir:
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            logger.add(
                str(Path(log_dir) / "engine.log"),
                level="INFO",
                rotation="10 MB",
                retention="14 days",
                backtrace=False,
                diagnose=False,
                enqueue=True,  # writes off-thread — never blocks the loop
            )
        except Exception:
            logger.warning("file log sink unavailable at {}", log_dir)
    _configured = True


def get_logger(name: str) -> Logger:
    """Return a module-bound logger."""
    _configure()
    return logger.bind(module=name)
