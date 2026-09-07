"""Engine version tag stored on signals, backtests, and paper trades."""

from __future__ import annotations

_V5 = "rule_based_v5_regimegate"
_V6 = "rule_based_v6_simple"


def _resolve_engine_version() -> str:
    import os

    if os.environ.get("SIMPLIFIED_STRATEGY", "").lower() in ("1", "true", "yes"):
        return _V6
    try:
        from app.config import get_settings

        if get_settings().simplified_strategy_enabled:
            return _V6
    except Exception:
        pass
    return _V5


def __getattr__(name: str):
    if name == "ENGINE_VERSION":
        return _resolve_engine_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

