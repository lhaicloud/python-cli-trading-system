from app.data.backfill import backfill, update_latest
from app.ta.signals import generate_signal
from app.backtesting.engine import run_backtest
from app.paper.trade_manager import run_paper_session
from app.models.trainer import train_model
from app.models.validator import validate_model, activate_model
from app.reports.reporter import print_general_report
from app.reports.backtest_report import print_detailed_backtest_report
from app.reports.learning_report import print_learning_report
print("All menu modules loaded OK")
