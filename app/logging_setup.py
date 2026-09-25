"""
Logging configuration for the whole application.

Standard library logging, configured once at the entry point. Every module
gets its own logger via ``logging.getLogger(__name__)`` so the module path
appears in the output and levels can be tuned per subsystem.

Two levels matter here:

  INFO   one readable line per pipeline step: what it was given, what it
         produced, how long it took. Safe to leave on in a deployment.
  DEBUG  the payloads themselves, prompts, retrieved text, graph facts.
         Verbose and can contain whole documents, so it is opt in.

Payload logging is additionally gated behind LOG_PAYLOADS, because DEBUG on
a noisy dependency should not start dumping documents into the log.

Nothing here logs an API key. Values read from the environment are reported
as set or unset only.
"""

import logging
import sys
from typing import Any

from app.config import LOG_LEVEL, LOG_PAYLOADS

_CONFIGURED = False

FORMAT = "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"
DATEFMT = "%H:%M:%S"


def setup_logging(level: str | None = None) -> None:
    """
    Configures the root logger once. Safe to call from several entry points
    (the API, the CLI scripts) because repeat calls are ignored.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATEFMT))

    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or LOG_LEVEL), logging.INFO))
    root.handlers[:] = [handler]

    # These are chatty at DEBUG and drown out anything useful.
    for noisy in (
        "httpx", "httpx2", "httpcore", "urllib3", "neo4j", "chromadb",
        "sentence_transformers", "transformers", "torch",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    logging.getLogger(__name__).debug(
        "logging configured: level=%s payloads=%s", root.level, LOG_PAYLOADS
    )


def payload(log: logging.Logger, label: str, value: Any) -> None:
    """
    Logs a payload at DEBUG, but only when LOG_PAYLOADS is set.

    Kept as a helper so call sites stay one line and so the guard cannot be
    forgotten at one of them, which is how documents end up in production
    logs.
    """
    if LOG_PAYLOADS and log.isEnabledFor(logging.DEBUG):
        log.debug("%s: %s", label, value)
