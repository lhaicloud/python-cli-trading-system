"""Retrain models for all newly validated coins."""
from app.models.trainer import train_model
from app.models.validator import validate_model, activate_model
from app.models.versioning import model_file_path

MIN_F1 = 0.50
SYMBOLS = ["AVAXUSDT", "LINKUSDT", "XLMUSDT", "ZECUSDT",
           "DOTUSDT",  "XRPUSDT",  "ARBUSDT", "DOGEUSDT"]
PARAMS = {"algorithm": "gbm", "n_estimators": 200, "max_depth": 4, "learning_rate": 0.10}

trained = {}
for symbol in SYMBOLS:
    print(f"\n{'='*50}\n  {symbol}\n{'='*50}")
    trained[symbol] = {}
    for mt in ["buy", "sell"]:
        p = dict(PARAMS); algo = p.pop("algorithm")
        try:
            r = train_model(symbol, mt, algorithm=algo, **p)
            trained[symbol][mt] = r["version"]
            print(f"  {mt.upper()} cv_f1={r['cv_f1_mean']:.3f}  n={r['samples']}")
        except Exception as e:
            print(f"  {mt.upper()} ERROR: {e}")
            trained[symbol][mt] = None

print("\n\nValidating...\n")
for symbol in SYMBOLS:
    for mt in ["buy", "sell"]:
        v = trained[symbol].get(mt)
        if not v:
            print(f"  [{symbol} {mt.upper()}] Skipped")
            continue
        try:
            rpt = validate_model(symbol, mt, model_file_path(symbol, mt, v))
            f1  = rpt.get("candidate_f1", 0.0)
            print(f"  [{symbol} {mt.upper()}] holdout_f1={f1:.3f}", end="  ")
            if f1 >= MIN_F1:
                activate_model(symbol, mt, v)
                print(f"ACTIVATED: {v}")
            else:
                print(f"SKIPPED (< {MIN_F1})")
        except Exception as e:
            print(f"  [{symbol} {mt.upper()}] Validate ERROR: {e}")

print("\nDone.")
