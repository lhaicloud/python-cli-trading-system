"""
Retrain all models using per-symbol tuned hyperparameters from ml_best_params.json.
Falls back to safe defaults if file not found.

Validation: uses true 80/20 holdout F1 with MIN_F1_TO_ACTIVATE threshold.
"""
import json
import pathlib

from app.models.trainer import train_model
from app.models.validator import validate_model, activate_model
from app.models.versioning import model_file_path

MIN_F1_TO_ACTIVATE = 0.50

# Load tuned params (from ml_tune_and_train.py) or use safe defaults
PARAMS_FILE = pathlib.Path("ml_best_params.json")
if PARAMS_FILE.exists():
    TUNED_PARAMS = json.loads(PARAMS_FILE.read_text())
    print(f"Loaded tuned params from {PARAMS_FILE}")
else:
    TUNED_PARAMS = {
        "BTCUSDT": {
            "buy":  {"algorithm": "gbm", "n_estimators": 200, "max_depth": 4, "learning_rate": 0.10},
            "sell": {"algorithm": "gbm", "n_estimators": 200, "max_depth": 4, "learning_rate": 0.10},
        },
        "ETHUSDT": {
            "buy":  {"algorithm": "gbm", "n_estimators": 200, "max_depth": 4, "learning_rate": 0.10},
            "sell": {"algorithm": "gbm", "n_estimators": 100, "max_depth": 3, "learning_rate": 0.10},
        },
        "SOLUSDT": {
            "buy":  {"algorithm": "gbm", "n_estimators": 100, "max_depth": 3, "learning_rate": 0.15},
            "sell": {"algorithm": "gbm", "n_estimators": 100, "max_depth": 4, "learning_rate": 0.15},
        },
        "BNBUSDT": {
            "buy":  {"algorithm": "gbm", "n_estimators": 300, "max_depth": 3, "learning_rate": 0.05},
            "sell": {"algorithm": "gbm", "n_estimators": 100, "max_depth": 5, "learning_rate": 0.05},
        },
    }
    print("Using default params (run ml_tune_and_train.py first for best results)")

SYMBOLS     = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
MODEL_TYPES = ["buy", "sell"]

trained_versions = {}

for symbol in SYMBOLS:
    print(f"\n{'='*50}")
    print(f"  Training {symbol} models...")
    print(f"{'='*50}\n")
    trained_versions[symbol] = {}
    for model_type in MODEL_TYPES:
        p = TUNED_PARAMS.get(symbol, {}).get(model_type, {})
        algo   = p.pop("algorithm", "gbm") if isinstance(p, dict) else "gbm"
        _valid = ("n_estimators", "max_depth", "learning_rate", "min_samples_leaf", "max_features")
        params = {k: v for k, v in p.items() if k in _valid}

        print(f"Training {model_type.upper()} [{algo.upper()}] for {symbol}...")
        print(f"  Params: {params}")
        try:
            result = train_model(symbol, model_type, algorithm=algo, **params)
            version = result["version"]
            trained_versions[symbol][model_type] = version
            print(f"  Version:     {version}")
            print(f"  Samples:     {result['samples']} ({result['wins']}W / {result['losses']}L)")
            print(f"  Win rate:    {result['win_rate']:.1%}")
            print(f"  CV F1:       {result['cv_f1_mean']:.4f} +/- {result['cv_f1_std']:.4f}")
            print(f"  Train acc.:  {result['train_accuracy']:.1%}")
        except Exception as ex:
            print(f"  ERROR: {ex}")
            trained_versions[symbol][model_type] = None


print("\n\nAll models trained. Validating on true holdout...\n")

for symbol in SYMBOLS:
    print(f"\n{'='*50}")
    print(f"  Validating {symbol} models...")
    print(f"{'='*50}\n")
    for model_type in MODEL_TYPES:
        version = trained_versions[symbol].get(model_type)
        if version is None:
            print(f"  [{model_type.upper()}] Skipped — training failed")
            continue

        candidate_path = model_file_path(symbol, model_type, version)
        print(f"Validating {model_type.upper()} for {symbol}...")
        try:
            report = validate_model(symbol, model_type, candidate_path)
            cand_f1 = report.get("candidate_f1", 0.0)
            test_n  = report.get("test_samples", 0)
            wr      = report.get("candidate_win_rate", 0.0)

            print(f"  Holdout F1:    {cand_f1:.4f}  ({test_n} samples)")
            print(f"  Win rate:      {wr:.1%}")

            if isinstance(cand_f1, float) and cand_f1 >= MIN_F1_TO_ACTIVATE:
                activate_model(symbol, model_type, version)
                print(f"  ACTIVATED: {version}")
            else:
                print(f"  SKIPPED: holdout F1 {cand_f1:.4f} < {MIN_F1_TO_ACTIVATE}")
        except Exception as ex:
            print(f"  ERROR: {ex}")

print("\n\nDone. All models trained and validated.")
