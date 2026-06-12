"""
Retrain ML models for newly validated coins (AVAX, LINK).
Uses same GBM defaults as ml_retrain_tuned.py.
Run after seed_training_data.py to include their backtest snapshots.
"""
from app.models.trainer import train_model
from app.models.validator import validate_model, activate_model
from app.models.versioning import model_file_path

MIN_F1_TO_ACTIVATE = 0.50

SYMBOLS = ["AVAXUSDT", "LINKUSDT", "XLMUSDT", "ZECUSDT"]
MODEL_TYPES = ["buy", "sell"]

DEFAULT_PARAMS = {
    "buy":  {"algorithm": "gbm", "n_estimators": 200, "max_depth": 4, "learning_rate": 0.10},
    "sell": {"algorithm": "gbm", "n_estimators": 200, "max_depth": 4, "learning_rate": 0.10},
}

trained = {}

for symbol in SYMBOLS:
    print(f"\n{'='*50}")
    print(f"  Training {symbol} models...")
    print(f"{'='*50}\n")
    trained[symbol] = {}
    for model_type in MODEL_TYPES:
        p    = dict(DEFAULT_PARAMS[model_type])
        algo = p.pop("algorithm")
        print(f"Training {model_type.upper()} [{algo.upper()}] for {symbol}...")
        try:
            result  = train_model(symbol, model_type, algorithm=algo, **p)
            version = result["version"]
            trained[symbol][model_type] = version
            print(f"  Version:     {version}")
            print(f"  Samples:     {result['samples']} ({result['wins']}W / {result['losses']}L)")
            print(f"  Win rate:    {result['win_rate']:.1%}")
            print(f"  CV F1:       {result['cv_f1_mean']:.4f} +/- {result['cv_f1_std']:.4f}")
        except Exception as ex:
            print(f"  ERROR: {ex}")
            trained[symbol][model_type] = None

print("\n\nValidating on true holdout...\n")

for symbol in SYMBOLS:
    print(f"\n{'='*50}")
    print(f"  Validating {symbol} models...")
    print(f"{'='*50}\n")
    for model_type in MODEL_TYPES:
        version = trained[symbol].get(model_type)
        if version is None:
            print(f"  [{model_type.upper()}] Skipped — training failed")
            continue
        candidate_path = model_file_path(symbol, model_type, version)
        print(f"Validating {model_type.upper()} for {symbol}...")
        try:
            report   = validate_model(symbol, model_type, candidate_path)
            cand_f1  = report.get("candidate_f1", 0.0)
            test_n   = report.get("test_samples", 0)
            wr       = report.get("candidate_win_rate", 0.0)
            print(f"  Holdout F1:    {cand_f1:.4f}  ({test_n} samples)")
            print(f"  Win rate:      {wr:.1%}")
            if isinstance(cand_f1, float) and cand_f1 >= MIN_F1_TO_ACTIVATE:
                activate_model(symbol, model_type, version)
                print(f"  ACTIVATED: {version}")
            else:
                print(f"  SKIPPED (F1 {cand_f1:.4f} < {MIN_F1_TO_ACTIVATE} threshold) — rule-based fallback remains")
        except Exception as ex:
            print(f"  ERROR: {ex}")

print("\nDone.")
