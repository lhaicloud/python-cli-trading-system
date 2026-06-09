#!/usr/bin/env python3
"""LQ-MTF Strategy — CLI entry point."""

import sys
import io

# Force UTF-8 output on Windows so Rich emoji renders correctly
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

from app.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
