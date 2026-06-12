"""
Train candle-based BUY/SELL classifiers for all 4 main symbols.

Unlike signal-model training (which uses ~1k feature_snapshots per symbol),
candle models label every 30M candle by forward price action — giving 40-70k
samples per symbol with zero circular feedback from the rule engine.

buy_model.py blends: 60% candle model + 40% signal model when both are active.
"""
import warnings
warnings.filterwarnings('ignore')

import logging
logging.basicConfig(
    filename="data/candle_train.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

from dotenv import load_dotenv
load_dotenv()
from app.db.migrations import run_migrations
run_migrations()

from app.models.trainer import train_from_candles
from app.models.versioning import model_file_path
from app.models.validator import activate_model

MIN_CV_F1 = 0.52

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
MODEL_TYPES = ["buy", "sell"]

results = []

for symbol in SYMBOLS:
    logging.info("=== Training candle models for %s ===", symbol)
    for model_type in MODEL_TYPES:
        candle_type = f"{model_type}_candle"
        logging.info("  [%s] starting...", candle_type)
        try:
            r = train_from_candles(
                symbol,
                model_type,
                n_estimators=300,
                max_depth=5,
                learning_rate=0.05,
                algorithm="gbm",
            )
            version = r["version"]
            cv_f1   = r["cv_f1_mean"]
            logging.info("  [%s] samples=%d wins=%d losses=%d wr=%.1%%",
                         candle_type, r['samples'], r['wins'], r['losses'], r['win_rate']*100)
            logging.info("  [%s] cv_f1=%.4f +/- %.4f  train_acc=%.1%%",
                         candle_type, cv_f1, r['cv_f1_std'], r['train_accuracy']*100)
            logging.info("  [%s] version=%s", candle_type, version)

            if cv_f1 >= MIN_CV_F1:
                activate_model(symbol, candle_type, version)
                logging.info("  [%s] ACTIVATED (cv_f1=%.4f >= %.2f)", candle_type, cv_f1, MIN_CV_F1)
                results.append((symbol, candle_type, cv_f1, "ACTIVATED"))
            else:
                logging.info("  [%s] SKIPPED (cv_f1=%.4f < %.2f)", candle_type, cv_f1, MIN_CV_F1)
                results.append((symbol, candle_type, cv_f1, "SKIPPED"))

        except Exception as exc:
            logging.exception("  [%s] ERROR: %s", candle_type, exc)
            results.append((symbol, candle_type, 0.0, f"ERROR: {exc}"))

logging.info("=== SUMMARY ===")
for sym, mtype, cv, status in results:
    logging.info("  %s %s  cv_f1=%.4f  %s", sym, mtype, cv, status)

logging.info("Done.")
