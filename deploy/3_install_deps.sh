#!/bin/bash
# Run this on the server AFTER the push script completes.
# Installs Python dependencies into the venv.

set -e
APP_DIR="/home/ubuntu/lqmtf"

echo "Installing Python dependencies..."
cd "$APP_DIR"
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt

echo ""
echo "Testing imports..."
python -c "
import typer, rich, pandas, numpy, httpx, sqlalchemy
import sklearn, joblib, pydantic, dotenv
print('All imports OK')
"

echo ""
echo "Running DB migrations..."
python -c "from app.db.migrations import run_migrations; run_migrations(); print('DB OK')"

echo ""
echo "Done. Start the service with:  sudo systemctl start lqmtf"
