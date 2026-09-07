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


def canonical_model_dir(model_dir: Path | str) -> Path:
    """Collapse a doubled trailing directory name: data/models/models → data/models.

    Only the specific `.../models/models` nest is flattened. MODEL_DIR itself is
    otherwise left alone so a deliberate directory name is not rewritten.
    """
    path = Path(model_dir)
    if path.name == "models" and path.parent.name == "models":
        return path.parent
    return path


def join_model_dir(model_dir: Path | str, path: str | Path) -> Path:
    """Join a model filename onto model_dir exactly once.

    Always uses the basename of `path`, so a registered or constructed value
    that already includes `data/models` (or `models/`) cannot produce
    `data/models/models/<file>`.
    """
    directory = canonical_model_dir(model_dir)
    name = Path(path).name
    if not name:
        raise ValueError("model filename is empty")
    return directory / name


def model_file_path(symbol: str, model_type: str, version: str) -> Path:
    cfg = get_settings()
    filename = f"{symbol.lower()}_{model_type}_{version}.joblib"
    return join_model_dir(cfg.model_dir, filename)


def nested_model_fallback_paths(model_dir: Path | str, registered: Path | str) -> list[Path]:
    """Candidate paths under the doubled `{model_dir.name}` nest.

    Used only when MODEL_PATH_FALLBACK_NESTED is on (footgun: can enable ML).
    """
    directory = Path(model_dir)
    name = Path(registered).name
    if not name:
        return []
    candidates = [
        directory / directory.name / name,
        directory / "models" / name,
    ]
    # De-dupe while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for cand in candidates:
        key = str(cand)
        if key not in seen:
            seen.add(key)
            out.append(cand)
    return out


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
    cfg = get_settings()
    stored = join_model_dir(cfg.model_dir, file_path)
    return save_model_version({
        "symbol":          symbol,
        "model_type":      model_type,
        "version":         version,
        "algorithm":       algorithm,
        "features":        features,
        "hyperparameters": hyperparameters,
        "file_path":       str(stored),
        "status":          status,
    })


def get_current_model_path(symbol: str, model_type: str) -> Path | None:
    """Return the active model file if it exists at the registered path.

    Missing files return None (callers score 0.0). Nested-directory fallback
    is OFF by default — enabling MODEL_PATH_FALLBACK_NESTED can suddenly
    load models that were inert because their registered path did not exist.
    """
    mv = get_active_model(symbol, model_type)
    if not mv or not mv.get("file_path"):
        return None
    p = Path(mv["file_path"])
    if p.exists():
        return p

    cfg = get_settings()
    if not cfg.model_path_fallback_nested:
        return None

    for candidate in nested_model_fallback_paths(cfg.model_dir, p):
        if candidate.exists():
            logger.warning(
                "MODEL_PATH_FALLBACK_NESTED found %s/%s at %s "
                "(registered path missing: %s) — this can enable previously inert ML",
                symbol, model_type, candidate, p,
            )
            return candidate
    return None
