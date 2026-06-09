#!/bin/bash
# Run this ONCE on the GCP server after first SSH login.
# ssh -i ~/.ssh/gcp_key lhaicloud@<YOUR_SERVER_IP>
# Then: bash 1_setup_server_gcp.sh

set -e

# Detect home dir and username dynamically
APP_USER="$(whoami)"
APP_DIR="/home/$APP_USER/lqmtf"
PYTHON="python3.11"

echo "=== [1/7] System update ==="
sudo apt-get update -q
sudo apt-get install -y -q \
    software-properties-common curl git unzip \
    build-essential libssl-dev libffi-dev \
    libsqlite3-dev sqlite3

echo "=== [2/7] Install Python 3.11 ==="
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt-get update -q
sudo apt-get install -y -q python3.11 python3.11-venv python3.11-dev

echo "=== [3/7] Create app directories ==="
mkdir -p "$APP_DIR/data/db"
mkdir -p "$APP_DIR/data/models"
mkdir -p "$APP_DIR/logs"

echo "=== [4/7] Add swap (1 GB) for low-RAM e2-micro ==="
if [ ! -f /swapfile ]; then
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap created."
else
    echo "Swap already exists, skipping."
fi

echo "=== [5/7] Create Python virtual environment ==="
cd "$APP_DIR"
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet

echo "=== [6/7] Install Python dependencies ==="
if [ -f "$APP_DIR/requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "WARNING: requirements.txt not found. Run push script first, then re-run:"
    echo "  cd $APP_DIR && source venv/bin/activate && pip install -r requirements.txt"
fi

echo "=== [7/7] Install systemd service ==="
# Write the service file with the correct user
sudo tee /etc/systemd/system/lqmtf.service > /dev/null <<EOF
[Unit]
Description=LQ-MTF Live Strategy Watcher
Documentation=https://github.com/lhaicloud/python-cli-trading-system
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR

Environment="PATH=$APP_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=$APP_DIR/.env

ExecStart=$APP_DIR/venv/bin/python main.py live \
    --auto \
    --sync \
    --auto-n 4 \
    --rescan-every 144 \
    --risk 1.0 \
    --heartbeat 8

Restart=always
RestartSec=60

StandardOutput=journal
StandardError=journal
SyslogIdentifier=lqmtf

KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable lqmtf

echo ""
echo "===================================================="
echo "  Server setup complete."
echo "  User: $APP_USER"
echo "  App:  $APP_DIR"
echo ""
echo "  Next steps:"
echo "  1. Run the push script from your local machine."
echo "  2. Create $APP_DIR/.env (see deploy/env.example)"
echo "  3. Start the watcher:  sudo systemctl start lqmtf"
echo "  4. Watch logs:         journalctl -u lqmtf -f"
echo "===================================================="
