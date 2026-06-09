"""Print feature importances for all active models."""
import joblib
import numpy as np
from app.models.versioning import model_file_path, get_active_model

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
TYPES   = ["buy", "sell"]

for symbol in SYMBOLS:
    for mt in TYPES:
        active = get_active_model(symbol, mt)
        version = active.get("version") if active else None
        if not version:
            print(f"{symbol} {mt.upper()}: no active model")
            continue
        path = model_file_path(symbol, mt, version)
        if not path.exists():
            print(f"{symbol} {mt.upper()}: file not found")
            continue
        data = joblib.load(path)
        pipe = data.get("pipeline") or data.get("model")
        features = data.get("features", [])
        cv_f1 = data.get("cv_f1", None)

        # Extract classifier from pipeline
        clf = pipe.named_steps.get("clf") or pipe.steps[-1][1]
        if not hasattr(clf, "feature_importances_"):
            print(f"{symbol} {mt.upper()}: no feature_importances_ (algo={type(clf).__name__})")
            continue

        fi = clf.feature_importances_
        ranked = sorted(zip(features, fi), key=lambda x: -x[1])

        q = f"cv_f1={cv_f1:.3f}" if cv_f1 else "cv_f1=?"
        print(f"\n{symbol} {mt.upper()} [{q}]")
        for feat, imp in ranked:
            bar = "#" * int(imp * 50)
            print(f"  {feat:<20} {imp:.4f}  {bar}")
