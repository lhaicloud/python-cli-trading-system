"""Centralised logging setup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    try:
        from app.config import get_settings
        cfg = get_settings()
        level = getattr(logging, cfg.log_level.upper(), logging.INFO)
        log_file: Path = cfg.log_file
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        level = logging.INFO
        log_file = Path("data/lqmtf.log")

    fmt = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    # httpx logs full request URLs at INFO — including the Telegram bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(name)
