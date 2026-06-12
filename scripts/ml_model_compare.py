"""
Compare GradientBoosting vs XGBoost vs RandomForest on temporal split.
Helps decide the best algorithm per symbol before final retrain.
"""
import numpy as np
import warnings
warnings.filterwarnings("ignore")

from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import f1_score
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from app.data.repository import get_feature_snapshots
from app.models.trainer import _snapshots_to_df, _FEATURE_COLS

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
MODEL_TYPES = ["buy", "sell"]

MODELS = {
    "GBM":  lambda: GradientBoostingClassifier(n_estimators=200, max_depth=4, learning_rate=0.1, subsample=0.8, random_state=42),
    "XGB":  lambda: XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42, verbosity=0),
    "RF":   lambda: RandomForestClassifier(n_estimators=200, max_depth=6, min_samples_leaf=5, random_state=42),
}

print(f"{'Symbol':<10} {'Type':<5} {'Model':<5}  {'CV F1':>7}  {'Hold F1':>8}  {'WR':>5}")
print("-" * 52)

all_results = {}

for symbol in SYMBOLS:
    snapshots = get_feature_snapshots(symbol)
    for model_type in MODEL_TYPES:
        df = _snapshots_to_df(snapshots, model_type)
        if df.empty or len(df) < 40:
            continue

        X = df[_FEATURE_COLS].values
        y = df["label"].values
        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        if y_train.sum() < 5 or (len(y_train) - y_train.sum()) < 5:
            continue
        if y_test.sum() < 2 or (len(y_test) - y_test.sum()) < 2:
            continue

        key = (symbol, model_type)
        all_results[key] = {}
        for name, clf_factory in MODELS.items():
            pipe = Pipeline([("scaler", StandardScaler()), ("clf", clf_factory())])
            cv = TimeSeriesSplit(n_splits=min(5, int(y_train.sum()), int(len(y_train) - y_train.sum())))
            cv_scores = cross_val_score(pipe, X_train, y_train, cv=cv, scoring="f1")
            pipe.fit(X_train, y_train)
            y_pred = pipe.predict(X_test)
            hold_f1 = f1_score(y_test, y_pred, zero_division=0)
            preds_pos = y_pred == 1
            wr = float(y_test[preds_pos].mean()) if preds_pos.sum() > 0 else 0.0
            all_results[key][name] = {"cv": cv_scores.mean(), "hold": hold_f1, "wr": wr}
            print(f"  {symbol:<10} {model_type:<5} {name:<5}  {cv_scores.mean():>7.4f}  {hold_f1:>8.4f}  {wr:>5.1%}")

print("\n\nBest model per symbol (by holdout F1):")
wins = {"GBM": 0, "XGB": 0, "RF": 0}
for (sym, mt), results in all_results.items():
    best_name = max(results, key=lambda k: results[k]["hold"])
    best_hold = results[best_name]["hold"]
    wins[best_name] += 1
    print(f"  {sym} {mt.upper()}: {best_name}  (F1={best_hold:.4f})")

print(f"\nOverall wins: GBM={wins['GBM']}  XGB={wins['XGB']}  RF={wins['RF']}")
