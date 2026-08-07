"""Omega Logging — total firehose debug logger.

Appends EVERYTHING to a single plain-text log next to the config file.
Controlled by "omega_logging": true in config.json.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import traceback
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.loader import get_config_path

# Async lock for line-level atomicity under heavy concurrent load
_OMEGA_LOCK: asyncio.Lock | None = None
_OMEGA_LOGGER: logging.Logger | None = None
_OMEGA_HANDLER: logging.Handler | None = None


def _ensure_lock() -> asyncio.Lock:
    global _OMEGA_LOCK
    if _OMEGA_LOCK is None:
        _OMEGA_LOCK = asyncio.Lock()
    return _OMEGA_LOCK


def _redact_images_in_obj(obj: Any) -> Any:
    """Recursively replace base64 image strings with size placeholders."""
    if isinstance(obj, str):
        # Replace data:image/...;base64,... with a size hint
        def _repl(m: re.Match) -> str:
            full = m.group(0)
            return f"[image: <{len(full)} bytes>]"
        return re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", _repl, obj)
    if isinstance(obj, list):
        return [_redact_images_in_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _redact_images_in_obj(v) for k, v in obj.items()}
    return obj


def _fmt_payload(payload: Any) -> str:
    """Serialize payload: dicts -> pretty JSON, everything else -> repr."""
    payload = _redact_images_in_obj(payload)
    if isinstance(payload, dict) or isinstance(payload, list):
        try:
            return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        except Exception:
            return repr(payload)
    return repr(payload)


class _OmegaFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")
        banner = "=" * 80
        payload = getattr(record, "omega_payload", None)
        return f"\n{banner}\n{ts} :: {record.name} :: {record.getMessage()}\n{banner}\n{_fmt_payload(payload)}"


def _omega_log_path() -> Path:
    return get_config_path().parent / "nanobot_omega.log"


def init_omega_logging(enabled: bool = False) -> None:
    """Initialize the omega logger if enabled and not already initialized.

    Any failure here is swallowed and logged via loguru so that a logging
    setup problem can never prevent nanobot from starting.
    """
    global _OMEGA_LOGGER, _OMEGA_HANDLER

    if not enabled:
        return

    if _OMEGA_LOGGER is not None:
        return  # already initialized

    try:
        log_path = _omega_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
        handler.setFormatter(_OmegaFormatter())
        handler.setLevel(logging.DEBUG)

        lg = logging.getLogger("OMEGA")
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
        lg.propagate = False

        _OMEGA_LOGGER = lg
        _OMEGA_HANDLER = handler

        # Capture all Python warnings
        _install_warning_capture()

        omega_log("OMEGA_INIT", {"log_path": str(log_path), "status": "initialized"})
    except Exception as exc:
        logger.warning("Omega logging initialization failed: {}", exc)
        logger.debug(traceback.format_exc())


def _install_warning_capture() -> None:
    """Override warnings.showwarning to pipe into omega log."""
    original_showwarning = warnings.showwarning

    def _omega_showwarning(message, category, filename, lineno, file=None, line=None):
        try:
            omega_log(
                "PYTHON_WARNING",
                {
                    "message": str(message),
                    "category": category.__name__,
                    "filename": filename,
                    "lineno": lineno,
                    "line": line,
                },
            )
        except Exception:
            pass
        # Still call original so stderr gets it too
        original_showwarning(message, category, filename, lineno, file, line)

    warnings.showwarning = _omega_showwarning


def omega_log(category: str, payload: Any) -> None:
    """Synchronous omega log entry."""
    if _OMEGA_LOGGER is None:
        return
    try:
        record = _OMEGA_LOGGER.makeRecord(
            _OMEGA_LOGGER.name,
            logging.DEBUG,
            "(omega)",
            0,
            category,
            (),
            None,
        )
        # Attach payload via custom attribute
        record.omega_payload = payload  # type: ignore[attr-defined]
        _OMEGA_LOGGER.handle(record)
    except Exception:
        # Never let logging itself crash the application
        pass


async def aomega_log(category: str, payload: Any) -> None:
    """Async-safe omega log entry with lock guarding."""
    if _OMEGA_LOGGER is None:
        return
    async with _ensure_lock():
        omega_log(category, payload)
