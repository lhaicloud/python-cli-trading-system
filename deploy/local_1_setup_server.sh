#!/bin/bash
# Run this ONCE on the local server as root.
# Adapted from 1_setup_server.sh for root user.

set -e

APP_DIR="/root/lqmtf"
PYTHON="python3"

echo "=== [1/6] System update ==="
apt-get update -q
apt-get install -y -q \
    software-properties-common curl git unzip \
    build-essential libssl-dev libffi-dev \
    libsqlite3-dev sqlite3 python3 python3-venv python3-dev python3-pip

echo "=== [2/6] Try Python 3.11 if available, else use system Python3 ==="
if ! python3.11 --version 2>/dev/null; then
    # Try deadsnakes PPA (Ubuntu/Debian)
    if command -v add-apt-repository &>/dev/null; then
        add-apt-repository ppa:deadsnakes/ppa -y 2>/dev/null || true
        apt-get update -q
        apt-get install -y -q python3.11 python3.11-venv python3.11-dev 2>/dev/null || true
    fi
fi

# Pick best available Python
if python3.11 --version &>/dev/null; then
    PYTHON="python3.11"
elif python3.10 --version &>/dev/null; then
    PYTHON="python3.10"
else
    PYTHON="python3"
fi
echo "Using: $($PYTHON --version)"

echo "=== [3/6] Create app directories ==="
mkdir -p "$APP_DIR/data/db"
mkdir -p "$APP_DIR/data/models"
mkdir -p "$APP_DIR/logs"

echo "=== [4/6] Create Python virtual environment ==="
cd "$APP_DIR"
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet

echo "=== [5/6] Install Python dependencies (if requirements.txt present) ==="
if [ -f "$APP_DIR/requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "WARNING: requirements.txt not found yet. Run push script first."
fi

echo "=== [6/6] Install systemd service ==="
if [ -f "$APP_DIR/deploy/lqmtf_root.service" ]; then
    cp "$APP_DIR/deploy/lqmtf_root.service" /etc/systemd/system/lqmtf.service
else
    cp "$APP_DIR/deploy/lqmtf.service" /etc/systemd/system/lqmtf.service
fi
systemctl daemon-reload
systemctl enable lqmtf

echo ""
echo "===================================================="
echo "  Server setup complete."
echo "  Next: run push script, then: systemctl start lqmtf"
echo "===================================================="
