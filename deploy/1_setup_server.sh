#!/bin/bash
# Run this ONCE on the OCI server after first SSH login.
# ssh ubuntu@<YOUR_SERVER_IP>
# Then: bash 1_setup_server.sh

set -e

APP_DIR="/home/ubuntu/lqmtf"
PYTHON="python3.11"

echo "=== [1/6] System update ==="
sudo apt-get update -q
sudo apt-get install -y -q \
    software-properties-common curl git unzip \
    build-essential libssl-dev libffi-dev \
    libsqlite3-dev sqlite3

echo "=== [2/6] Install Python 3.11 ==="
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt-get update -q
sudo apt-get install -y -q python3.11 python3.11-venv python3.11-dev

echo "=== [3/6] Create app directories ==="
mkdir -p "$APP_DIR/data/db"
mkdir -p "$APP_DIR/data/models"
mkdir -p "$APP_DIR/logs"

echo "=== [4/6] Create Python virtual environment ==="
cd "$APP_DIR"
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet

echo "=== [5/6] Install Python dependencies ==="
# requirements.txt will be rsynced by the push script; wait for it
if [ -f "$APP_DIR/requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "WARNING: requirements.txt not found. Run push script first, then re-run step 5:"
    echo "  cd $APP_DIR && source venv/bin/activate && pip install -r requirements.txt"
fi

echo "=== [6/6] Install systemd service ==="
sudo cp "$APP_DIR/deploy/lqmtf.service" /etc/systemd/system/lqmtf.service
sudo systemctl daemon-reload
sudo systemctl enable lqmtf

echo ""
echo "===================================================="
echo "  Server setup complete."
echo ""
echo "  Next steps:"
echo "  1. Run the push script from your local machine."
echo "  2. Create /home/ubuntu/lqmtf/.env (see deploy/env.example)"
echo "  3. Start the watcher:  sudo systemctl start lqmtf"
echo "  4. Watch logs:         journalctl -u lqmtf -f"
echo "===================================================="
