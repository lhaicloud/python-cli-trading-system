"""SELL model wrapper — predict and score a potential short setup."""

from __future__ import annotations

from app.models.trainer import load_model, predict_signal
from app.models.versioning import get_current_model_path
from app.utils.logger import get_logger

logger = get_logger(__name__)

# (symbol, model_type) -> Path | None
_path_cache:  dict[tuple, object] = {}
# path_str -> model_data dict
_model_cache: dict[str, dict]     = {}

_MIN_CV_F1 = 0.52   # models below this CV F1 are too noisy to apply


def score_sell_setup(symbol: str, features: dict) -> float:
    """
    Return a probability score (0–1) for a SELL setup.

    Loads both the signal-based model ("sell") and the candle-based model
    ("sell_candle") if available. When both exist they are blended:
    60 % candle model (independent of rule engine) + 40 % signal model.
    Falls back to 0.0 if neither model is active.
    """
    signal_model = _get_model(symbol, "sell")
    candle_model = _get_model(symbol, "sell_candle")

    if signal_model is None and candle_model is None:
        return 0.0

    proba_signal = _predict(signal_model, features, "SELL/signal") if signal_model else 0.0
    proba_candle = _predict(candle_model, features, "SELL/candle") if candle_model else 0.0

    if signal_model is None:
        return round(proba_candle, 4)
    if candle_model is None:
        return round(proba_signal, 4)

    # Both available: candle model is more independent so weighted higher
    return round(0.6 * proba_candle + 0.4 * proba_signal, 4)


def invalidate_cache() -> None:
    """Clear caches (call after activating a new model version)."""
    _path_cache.clear()
    _model_cache.clear()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_model(symbol: str, model_type: str) -> dict | None:
    """Load model from cache or disk. Returns None if no active model or quality too low."""
    key = (symbol, model_type)
    if key not in _path_cache:
        _path_cache[key] = get_current_model_path(symbol, model_type)
    path = _path_cache[key]
    if path is None:
        return None
    try:
        path_str = str(path)
        if path_str not in _model_cache:
            _model_cache[path_str] = load_model(path)
        model_data = _model_cache[path_str]
        if model_data.get("cv_f1", 0.0) < _MIN_CV_F1:
            return None  # quality gate — too noisy
        return model_data
    except Exception as exc:
        logger.warning("[SELL model] Load failed for %s/%s: %s", symbol, model_type, exc)
        return None


def _predict(model_data: dict, features: dict, tag: str) -> float:
    try:
        _, proba = predict_signal(model_data, features)
        return proba
    except Exception as exc:
        logger.warning("[%s] Prediction failed: %s", tag, exc)
        return 0.0
