"""
Model trainer — BUY / SELL classifiers.

Uses feature snapshots + trade outcomes to train separate
GradientBoostingClassifier models for BUY and SELL signals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, TimeSeriesSplit, cross_val_score
try:
    from xgboost import XGBClassifier
    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report, confusion_matrix

from app.data.repository import (
    get_feature_snapshots,
    save_model_version,
)
from app.models.versioning import model_file_path, new_version_tag, register_model
from app.utils.logger import get_logger

logger = get_logger(__name__)

_FEATURE_COLS = [
    "h4_bias_score",
    "h1_conf_score",
    "zone_score",
    "liq_sweep",
    "pd_location_score",
    "rr_ratio",
    "volume_ratio",
    "rsi_14",
    "body_atr_ratio",
    "above_ema50",
    "above_ema200",
    "regime_score",
    "atr_expansion",
    "ema_alignment",
    "price_velocity",
    # Raw independent features (not derived from rule engine)
    "roc_10",
    "roc_20",
    "vol_trend",
    "htf_rsi_daily",
]


def _snapshots_to_df(snapshots: list[dict], model_type: str) -> pd.DataFrame:
    """Convert feature snapshot dicts into a training DataFrame."""
    rows = []
    for snap in snapshots:
        feats = snap.get("features", {})
        if not isinstance(feats, dict):
            continue
        outcome = snap.get("outcome")
        pnl     = snap.get("pnl", 0) or 0

        # For BUY model: positive outcome when pnl > 0
        # For SELL model: positive outcome when pnl > 0
        if model_type == "buy":
            direction = feats.get("signal") == "BUY"
        else:
            direction = feats.get("signal") == "SELL"

        if not direction:
            continue

        label = 1 if (outcome == "win" or pnl > 0) else 0
        row = {
            "label": label,
            "h4_bias_score":    _bias_to_score(feats.get("h4_bias", "neutral")),
            "h1_conf_score":    _conf_to_score(feats.get("h1_confirmation", "neutral")),
            "zone_score":       float(feats.get("zone_score", 50)),
            "liq_sweep":        int(bool(feats.get("liquidity_sweep", 0))),
            "pd_location_score": _pd_to_score(feats.get("premium_discount", "equilibrium"), model_type),
            "rr_ratio":         float(feats.get("risk_reward", 2)),
            "volume_ratio":     float(feats.get("volume_ratio", 1)),
            "rsi_14":           float(feats.get("rsi_14", 50)),
            "body_atr_ratio":   float(feats.get("body_atr_ratio", 1)),
            "above_ema50":      int(feats.get("above_ema50", 0)),
            "above_ema200":     int(feats.get("above_ema200", 0)),
            "regime_score":     _regime_to_score(feats.get("market_regime", ""), model_type),
            "atr_expansion":    float(feats.get("atr_expansion", 1.0)),
            "ema_alignment":    float(feats.get("ema_alignment", 1.5)),
            "price_velocity":   float(feats.get("price_velocity", 0.0)),
            # New raw features — default to neutral for older snapshots that lack them
            "roc_10":           float(feats.get("roc_10", 0.0)),
            "roc_20":           float(feats.get("roc_20", 0.0)),
            "vol_trend":        float(feats.get("vol_trend", 0.0)),
            "htf_rsi_daily":    float(feats.get("htf_rsi_daily", 50.0)),
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def train_model(
    symbol: str,
    model_type: str,
    n_estimators: int = 150,
    max_depth: int = 4,
    learning_rate: float = 0.05,
    algorithm: str = "gbm",  # "gbm", "xgb", "rf"
    min_samples_leaf: int = 5,
    max_features: object = "sqrt",
) -> dict[str, Any]:
    """
    Train a BUY or SELL classifier from stored feature snapshots.

    Returns a metrics dict. Raises ValueError if insufficient data.
    """
    if model_type not in ("buy", "sell"):
        raise ValueError(f"model_type must be 'buy' or 'sell', got {model_type!r}")

    snapshots = get_feature_snapshots(symbol)
    if len(snapshots) < 20:
        raise ValueError(
            f"Only {len(snapshots)} feature snapshots found for {symbol}. "
            "Need at least 20 labelled trades to train."
        )

    df = _snapshots_to_df(snapshots, model_type)
    if df.empty or len(df) < 10:
        raise ValueError(f"Insufficient {model_type.upper()} trade samples after filtering.")

    X = df[_FEATURE_COLS].values
    y = df["label"].values

    pos = y.sum()
    neg = len(y) - pos
    logger.info("[%s %s] Training: %d total snapshots (%d wins / %d losses)", symbol, model_type, len(y), pos, neg)

    if pos < 5 or neg < 5:
        raise ValueError("Need at least 5 winning and 5 losing examples to train reliably.")

    # Build classifier based on algorithm choice
    algo = algorithm.lower()
    if algo == "xgb":
        if not _HAS_XGB:
            raise ImportError("xgboost not installed. Run: pip install xgboost")
        clf = XGBClassifier(
            n_estimators=n_estimators, max_depth=max_depth, learning_rate=learning_rate,
            subsample=0.8, colsample_bytree=0.8, eval_metric="logloss",
            random_state=42, verbosity=0,
        )
    elif algo == "rf":
        clf = RandomForestClassifier(
            n_estimators=n_estimators, max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            max_features=max_features,
            random_state=42,
        )
    else:
        clf = GradientBoostingClassifier(
            n_estimators=n_estimators, max_depth=max_depth, learning_rate=learning_rate,
            subsample=0.8, random_state=42,
        )

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", clf),
    ])

    # Temporal split: train on first 80%, keep last 20% for holdout validation
    split = int(len(X) * 0.8)
    X_train, y_train = X[:split], y[:split]

    pos_train = int(y_train.sum())
    neg_train = len(y_train) - pos_train
    if pos_train < 5 or neg_train < 5:
        raise ValueError("Train split has fewer than 5 winning or losing examples.")

    # Temporal CV within train portion only
    n_splits = min(5, pos_train, neg_train)
    cv = TimeSeriesSplit(n_splits=n_splits)
    cv_scores = cross_val_score(pipe, X_train, y_train, cv=cv, scoring="f1")

    # Final fit on train portion only (last 20% reserved as true holdout for validator)
    pipe.fit(X_train, y_train)

    y_pred = pipe.predict(X_train)
    report = classification_report(y_train, y_pred, output_dict=True)
    win_rate = float(pos / len(y))

    version = new_version_tag(model_type)
    file_path = model_file_path(symbol, model_type, version)
    joblib.dump({"pipeline": pipe, "features": _FEATURE_COLS, "cv_f1": float(cv_scores.mean())}, file_path)

    logger.info("[%s %s] Model saved: %s (cv_f1=%.3f, train_n=%d, holdout_n=%d)",
                symbol, model_type, version, cv_scores.mean(), len(X_train), len(X) - len(X_train))

    metrics = {
        "version":        version,
        "samples":        len(X_train),
        "wins":           int(pos_train),
        "losses":         int(neg_train),
        "win_rate":       round(win_rate, 4),
        "cv_f1_mean":     round(float(cv_scores.mean()), 4),
        "cv_f1_std":      round(float(cv_scores.std()), 4),
        "train_accuracy": round(float((y_pred == y_train).mean()), 4),
        "report":         report,
    }

    register_model(
        symbol=symbol,
        model_type=model_type,
        version=version,
        algorithm=algo.upper(),
        features=_FEATURE_COLS,
        hyperparameters={
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "algorithm": algo,
        },
        file_path=file_path,
        status="candidate",
    )

    return metrics


def train_from_candles(
    symbol: str,
    model_type: str,
    n_estimators: int = 150,
    max_depth: int = 4,
    learning_rate: float = 0.05,
    algorithm: str = "gbm",
) -> dict[str, Any]:
    """
    Train a candle-based classifier using independently-labeled price data.

    Unlike train_model() which trains on signal-engine trade outcomes, this
    function labels every candle by forward price action (did price move
    atr_multiple × ATR in this direction before reversing?). This produces
    far more training samples and avoids circular feedback with the rule engine.

    Model is saved under model_type "buy_candle" or "sell_candle" so it
    coexists with the signal-based model without replacing it.
    """
    if model_type not in ("buy", "sell"):
        raise ValueError(f"model_type must be 'buy' or 'sell', got {model_type!r}")

    from app.models.candle_labeler import label_candles, CANDLE_FEATURE_COLS

    candle_model_type = f"{model_type}_candle"

    df = label_candles(symbol)
    if df.empty:
        raise ValueError(f"No candle data found for {symbol}. Run backfill first.")

    # Keep only decisive outcomes (drop inconclusive label=0)
    df = df[df["label"] != 0].copy()

    if model_type == "buy":
        df["label"] = (df["label"] == 1).astype(int)
    else:
        # For sell model: bearish outcome (-1) is a win
        df["label"] = (df["label"] == -1).astype(int)

    if len(df) < 50:
        raise ValueError(
            f"Only {len(df)} labeled candles for {model_type} direction. "
            "Need at least 50 decisive outcomes. Run more backfill data."
        )

    X = df[CANDLE_FEATURE_COLS].values
    y = df["label"].values

    pos = int(y.sum())
    neg = len(y) - pos
    logger.info(
        "[%s %s_candle] Training: %d candles (%d bullish / %d bearish)",
        symbol, model_type, len(y), pos, neg,
    )

    if pos < 10 or neg < 10:
        raise ValueError("Need at least 10 positive and 10 negative labeled candles.")

    algo = algorithm.lower()
    if algo == "xgb":
        if not _HAS_XGB:
            raise ImportError("xgboost not installed. Run: pip install xgboost")
        clf = XGBClassifier(
            n_estimators=n_estimators, max_depth=max_depth, learning_rate=learning_rate,
            subsample=0.8, colsample_bytree=0.8, eval_metric="logloss",
            random_state=42, verbosity=0,
        )
    elif algo == "rf":
        clf = RandomForestClassifier(
            n_estimators=n_estimators, max_depth=max_depth,
            min_samples_leaf=5, random_state=42,
        )
    else:
        clf = GradientBoostingClassifier(
            n_estimators=n_estimators, max_depth=max_depth, learning_rate=learning_rate,
            subsample=0.8, random_state=42,
        )

    pipe = Pipeline([("scaler", StandardScaler()), ("clf", clf)])

    split = int(len(X) * 0.8)
    X_train, y_train = X[:split], y[:split]

    pos_train = int(y_train.sum())
    neg_train = len(y_train) - pos_train
    if pos_train < 5 or neg_train < 5:
        raise ValueError("Train split has too few examples of one class.")

    n_splits = min(5, pos_train, neg_train)
    cv = TimeSeriesSplit(n_splits=n_splits)
    cv_scores = cross_val_score(pipe, X_train, y_train, cv=cv, scoring="f1")

    pipe.fit(X_train, y_train)
    y_pred = pipe.predict(X_train)
    report = classification_report(y_train, y_pred, output_dict=True)

    version   = new_version_tag(candle_model_type)
    file_path = model_file_path(symbol, candle_model_type, version)
    joblib.dump(
        {"pipeline": pipe, "features": CANDLE_FEATURE_COLS, "cv_f1": float(cv_scores.mean())},
        file_path,
    )

    logger.info(
        "[%s %s_candle] Model saved: %s (cv_f1=%.3f, train_n=%d, holdout_n=%d)",
        symbol, model_type, version, cv_scores.mean(), len(X_train), len(X) - len(X_train),
    )

    metrics = {
        "version":        version,
        "samples":        len(X_train),
        "wins":           pos_train,
        "losses":         neg_train,
        "win_rate":       round(float(pos / len(y)), 4),
        "cv_f1_mean":     round(float(cv_scores.mean()), 4),
        "cv_f1_std":      round(float(cv_scores.std()), 4),
        "train_accuracy": round(float((y_pred == y_train).mean()), 4),
        "report":         report,
    }

    register_model(
        symbol=symbol,
        model_type=candle_model_type,
        version=version,
        algorithm=algo.upper(),
        features=CANDLE_FEATURE_COLS,
        hyperparameters={
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "algorithm": algo,
        },
        file_path=file_path,
        status="candidate",
    )

    return metrics


def load_model(file_path: Path) -> dict:
    if not file_path.exists():
        raise FileNotFoundError(f"Model file not found: {file_path}")
    return joblib.load(file_path)


def predict_signal(model_data: dict, features: dict) -> tuple[int, float]:
    """Return (label, probability) for a given feature dict."""
    pipe = model_data["pipeline"]
    feat_cols = model_data["features"]
    X = np.array([[features.get(f, 0) for f in feat_cols]])
    label = int(pipe.predict(X)[0])
    proba = float(pipe.predict_proba(X)[0][1])
    return label, proba


# ── Feature encoding helpers ──────────────────────────────────────────────────

def _bias_to_score(bias: str) -> float:
    mapping = {
        "strongly_bullish": 2.0,
        "bullish":          1.0,
        "neutral":          0.0,
        "bearish":         -1.0,
        "strongly_bearish":-2.0,
    }
    return mapping.get(bias, 0.0)


def _conf_to_score(conf: str) -> float:
    return {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}.get(conf, 0.0)


def _pd_to_score(location: str, model_type: str) -> float:
    if model_type == "buy":
        return {"deep_discount": 2.0, "discount": 1.0, "equilibrium": 0.0,
                "premium": -1.0}.get(location, 0.0)
    else:
        return {"premium": 2.0, "equilibrium": 0.0,
                "discount": -1.0, "deep_discount": -2.0}.get(location, 0.0)


def _regime_to_score(regime: str, model_type: str) -> float:
    bullish_regimes = {"bullish_trend": 2.0, "accumulation": 1.0, "trap_zone": 0.5}
    bearish_regimes = {"bearish_trend": 2.0, "distribution": 1.0, "trap_zone": 0.5}
    if model_type == "buy":
        return bullish_regimes.get(regime, 0.0)
    else:
        return bearish_regimes.get(regime, 0.0)
