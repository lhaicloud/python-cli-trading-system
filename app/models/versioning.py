"""Model versioning utilities."""

from __future__ import annotations

import time
from pathlib import Path

from app.config import get_settings
from app.data.repository import get_active_model, save_model_version
from app.utils.logger import get_logger

logger = get_logger(__name__)


def new_version_tag(model_type: str) -> str:
    ts = int(time.time())
    return f"{model_type}_v_{ts}"


def model_file_path(symbol: str, model_type: str, version: str) -> Path:
    cfg = get_settings()
    return cfg.model_dir / f"{symbol.lower()}_{model_type}_{version}.joblib"


def register_model(
    symbol: str,
    model_type: str,
    version: str,
    algorithm: str,
    features: list[str],
    hyperparameters: dict,
    file_path: Path,
    status: str = "candidate",
) -> int:
    return save_model_version({
        "symbol":          symbol,
        "model_type":      model_type,
        "version":         version,
        "algorithm":       algorithm,
        "features":        features,
        "hyperparameters": hyperparameters,
        "file_path":       str(file_path),
        "status":          status,
    })


def get_current_model_path(symbol: str, model_type: str) -> Path | None:
    mv = get_active_model(symbol, model_type)
    if not mv or not mv.get("file_path"):
        return None
    p = Path(mv["file_path"])
    return p if p.exists() else None
