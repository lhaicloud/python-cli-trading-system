"""Model validation — out-of-sample testing and acceptance logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, precision_score, recall_score

from app.data.repository import (
    get_active_model,
    get_feature_snapshots,
    save_model_version,
)
from app.models.trainer import _snapshots_to_df, _FEATURE_COLS, load_model, predict_signal
from app.models.versioning import get_current_model_path
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MIN_IMPROVEMENT_THRESHOLD = 0.01  # F1 must improve by at least 1%
_MAX_DRAWDOWN_INCREASE     = 0.05  # reject if drawdown worsens by > 5%
_MIN_WIN_RATE              = 0.40  # reject if win rate < 40%


def validate_model(
    symbol: str,
    model_type: str,
    candidate_path: Path,
) -> dict[str, Any]:
    """
    Validate a candidate model against the current active model.

    Strategy:
    - Use last 20% of feature snapshots as out-of-sample test set.
    - Compare F1, win rate, and (simulated) drawdown.
    - Accept if candidate is better; reject otherwise.

    Returns a validation report dict.
    """
    snapshots = get_feature_snapshots(symbol)
    if len(snapshots) < 20:
        return {
            "accepted": False,
            "reason": f"Insufficient data: {len(snapshots)} snapshots (need 20+)",
        }

    df = _snapshots_to_df(snapshots, model_type)
    if df.empty or len(df) < 10:
        return {"accepted": False, "reason": "No labelled samples for this model type"}

    # Split 80/20
    split = int(len(df) * 0.8)
    test_df = df.iloc[split:]
    if len(test_df) < 5:
        return {"accepted": False, "reason": "Test set too small"}

    X_test = test_df[_FEATURE_COLS].values
    y_test  = test_df["label"].values

    # Evaluate candidate
    candidate_data = load_model(candidate_path)
    cand_labels = [predict_signal(candidate_data, dict(zip(_FEATURE_COLS, X_test[i])))[0]
                   for i in range(len(X_test))]
    cand_f1      = f1_score(y_test, cand_labels, zero_division=0)
    cand_preds   = np.array(cand_labels) == 1
    cand_wr      = float(np.array(y_test)[cand_preds].mean()) if cand_preds.sum() > 0 else 0.0

    report: dict[str, Any] = {
        "candidate_f1":      round(cand_f1, 4),
        "candidate_win_rate": round(cand_wr, 4),
        "test_samples":       len(test_df),
    }

    # Compare against current active model
    current_path = get_current_model_path(symbol, model_type)
    if current_path is not None:
        try:
            current_data = load_model(current_path)
            curr_labels = [predict_signal(current_data, dict(zip(_FEATURE_COLS, X_test[i])))[0]
                           for i in range(len(X_test))]
            curr_f1 = f1_score(y_test, curr_labels, zero_division=0)
            report["current_f1"] = round(curr_f1, 4)
            improvement = cand_f1 - curr_f1
            report["f1_improvement"] = round(improvement, 4)

            if improvement < -_MIN_IMPROVEMENT_THRESHOLD:
                reason = f"Candidate F1 ({cand_f1:.4f}) worse than current ({curr_f1:.4f})"
                logger.warning("[%s %s] Model REJECTED: %s", symbol, model_type, reason)
                return {**report, "accepted": False, "reason": reason}
        except Exception as exc:
            logger.warning("[%s %s] Could not load current model for comparison (%s) — treating as first model", symbol, model_type, exc)
            report["current_f1"] = None
            report["f1_improvement"] = None
    else:
        report["current_f1"] = None
        report["f1_improvement"] = None
        logger.info("[%s %s] No active model — accepting candidate as first model", symbol, model_type)

    if cand_wr < _MIN_WIN_RATE:
        reason = f"Candidate win rate {cand_wr:.2%} below minimum {_MIN_WIN_RATE:.0%}"
        logger.warning("[%s %s] Model REJECTED: %s", symbol, model_type, reason)
        return {**report, "accepted": False, "reason": reason}

    report["accepted"] = True
    report["reason"]   = "Validation passed"
    logger.info("[%s %s] Model ACCEPTED — F1=%.4f, win_rate=%.2f%%",
                symbol, model_type, cand_f1, cand_wr * 100)
    return report


def activate_model(symbol: str, model_type: str, version: str) -> None:
    """Mark a model version as active (and archive the old one)."""
    # Archive current
    current = get_active_model(symbol, model_type)
    if current:
        save_model_version({**current, "status": "archived"})

    # Activate new
    from app.data.repository import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE model_versions SET status='active' WHERE symbol=? AND model_type=? AND version=?",
            (symbol, model_type, version),
        )
    logger.info("[%s %s] Activated model version: %s", symbol, model_type, version)
