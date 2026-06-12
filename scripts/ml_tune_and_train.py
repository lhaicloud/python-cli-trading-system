"""
Hyperparameter tuning: GridSearchCV on GBM and XGBoost, picks best per symbol.
Saves results to ml_best_params.json for use by ml_retrain_tuned.py.

Uses temporal 80/20 split (no future leakage).
"""
import json
import warnings
warnings.filterwarnings("ignore")

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import f1_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from sklearn.ensemble import RandomForestClassifier
from app.data.repository import get_feature_snapshots
from app.models.trainer import _snapshots_to_df, _FEATURE_COLS

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
MODEL_TYPES = ["buy", "sell"]

GBM_GRID = {
    "clf__n_estimators":  [100, 200, 300],
    "clf__max_depth":     [3, 4, 5],
    "clf__learning_rate": [0.05, 0.10, 0.15],
    "clf__subsample":     [0.8],
    "clf__min_samples_leaf": [1, 3],
}
XGB_GRID = {
    "clf__n_estimators":  [100, 200, 300],
    "clf__max_depth":     [3, 4, 5],
    "clf__learning_rate": [0.05, 0.10, 0.15],
}
RF_GRID = {
    "clf__n_estimators":  [200, 400],
    "clf__max_depth":     [5, 8, None],
    "clf__min_samples_leaf": [1, 3, 5],
    "clf__max_features":  ["sqrt", 0.5],
}

best_params = {}

for symbol in SYMBOLS:
    snapshots = get_feature_snapshots(symbol)
    best_params[symbol] = {}
    print(f"\n{'='*52}")
    print(f"  {symbol}  ({len(snapshots)} snapshots)")
    print(f"{'='*52}")

    for model_type in MODEL_TYPES:
        df = _snapshots_to_df(snapshots, model_type)
        if df.empty or len(df) < 40:
            print(f"  [{model_type.upper()}] Skipped — only {len(df)} samples")
            continue

        X = df[_FEATURE_COLS].values
        y = df["label"].values
        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        pos_train = int(y_train.sum())
        neg_train = len(y_train) - pos_train
        pos_test  = int(y_test.sum())
        neg_test  = len(y_test) - pos_test

        print(f"\n  [{model_type.upper()}] {len(X)} total | "
              f"train={len(X_train)} ({pos_train}W/{neg_train}L) | "
              f"test={len(X_test)} ({pos_test}W/{neg_test}L)")

        if pos_train < 5 or neg_train < 5 or pos_test < 2 or neg_test < 2:
            print(f"  [{model_type.upper()}] Skipped — insufficient class balance")
            continue

        n_cv = min(5, pos_train, neg_train)
        cv = TimeSeriesSplit(n_splits=n_cv)

        # Try GBM
        gbm_pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", GradientBoostingClassifier(subsample=0.8, random_state=42)),
        ])
        gs_gbm = GridSearchCV(gbm_pipe, GBM_GRID, scoring="f1", cv=cv, n_jobs=-1)
        gs_gbm.fit(X_train, y_train)
        gbm_pred = gs_gbm.predict(X_test)
        gbm_hold = f1_score(y_test, gbm_pred, zero_division=0)
        gbm_p = {k.replace("clf__", ""): v for k, v in gs_gbm.best_params_.items()}
        print(f"  [{model_type.upper()}] GBM  cv={gs_gbm.best_score_:.4f}  hold={gbm_hold:.4f}  {gbm_p}")

        # Try XGB
        xgb_pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", XGBClassifier(subsample=0.8, colsample_bytree=0.8,
                                   eval_metric="logloss", random_state=42, verbosity=0)),
        ])
        gs_xgb = GridSearchCV(xgb_pipe, XGB_GRID, scoring="f1", cv=cv, n_jobs=-1)
        gs_xgb.fit(X_train, y_train)
        xgb_pred = gs_xgb.predict(X_test)
        xgb_hold = f1_score(y_test, xgb_pred, zero_division=0)
        xgb_p = {k.replace("clf__", ""): v for k, v in gs_xgb.best_params_.items()}
        print(f"  [{model_type.upper()}] XGB  cv={gs_xgb.best_score_:.4f}  hold={xgb_hold:.4f}  {xgb_p}")

        # Try RF
        rf_pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", RandomForestClassifier(random_state=42)),
        ])
        gs_rf = GridSearchCV(rf_pipe, RF_GRID, scoring="f1", cv=cv, n_jobs=-1)
        gs_rf.fit(X_train, y_train)
        rf_pred = gs_rf.predict(X_test)
        rf_hold = f1_score(y_test, rf_pred, zero_division=0)
        rf_p = {k.replace("clf__", ""): v for k, v in gs_rf.best_params_.items()}
        print(f"  [{model_type.upper()}] RF   cv={gs_rf.best_score_:.4f}  hold={rf_hold:.4f}  {rf_p}")

        # Select winner by holdout F1 (three-way)
        scores = {"gbm": gbm_hold, "xgb": xgb_hold, "rf": rf_hold}
        params = {"gbm": gbm_p, "xgb": xgb_p, "rf": rf_p}
        winner = max(scores, key=scores.__getitem__)
        chosen = {"algorithm": winner, **params[winner]}
        print(f"  [{model_type.upper()}] => {winner.upper()} wins "
              f"(hold {scores[winner]:.4f} | gbm={gbm_hold:.4f} xgb={xgb_hold:.4f} rf={rf_hold:.4f})")

        best_params[symbol][model_type] = chosen

print("\n\nSummary of best params:")
for sym, types in best_params.items():
    for mt, p in types.items():
        print(f"  {sym} {mt.upper()}: {p}")

import pathlib
pathlib.Path("ml_best_params.json").write_text(json.dumps(best_params, indent=2))
print("\nSaved to ml_best_params.json")
print("\nNow run: python ml_retrain_tuned.py")
