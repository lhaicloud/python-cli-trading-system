#!/usr/bin/env bash
# Full deployment to local server via password SSH (uses plink/pscp from PuTTY).
# Usage: bash deploy/local_deploy.sh <server-ip> <password> [user]
#
# Example:
#   bash deploy/local_deploy.sh 192.168.250.212 VirtualWorld10 root

set -e

SERVER_IP="${1:?Usage: $0 <server-ip> <password> [user]}"
PASSWORD="${2:?Need password}"
REMOTE_USER="${3:-root}"
REMOTE_DIR="/$REMOTE_USER/lqmtf"
[ "$REMOTE_USER" = "root" ] && REMOTE_DIR="/root/lqmtf"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="$(dirname "$SCRIPT_DIR")"

PLINK="/c/Program Files/PuTTY/plink"
PSCP="/c/Program Files/PuTTY/pscp"

SSH="$PLINK -pw $PASSWORD -batch -ssh $REMOTE_USER@$SERVER_IP"
SCP="$PSCP -pw $PASSWORD -batch"

echo "============================================"
echo "  LQ-MTF → Local Server deploy"
echo "  Target: $REMOTE_USER@$SERVER_IP:$REMOTE_DIR"
echo "============================================"

# Accept host key automatically on first connect
echo "y" | "$PLINK" -pw "$PASSWORD" -ssh "$REMOTE_USER@$SERVER_IP" "echo connected" 2>/dev/null || true

echo ""
echo "[1/5] Creating remote directories..."
$SSH "mkdir -p $REMOTE_DIR/data/db $REMOTE_DIR/data/models $REMOTE_DIR/logs $REMOTE_DIR/deploy"

echo ""
echo "[2/5] Syncing code (excluding data, venv, __pycache__)..."
# Use rsync if available, else pscp
if command -v rsync &>/dev/null; then
    PLINK_CMD="$PLINK -pw $PASSWORD -batch"
    rsync -avz --progress \
        -e "$PLINK_CMD" \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude '.env' \
        --exclude 'venv/' \
        --exclude '.git/' \
        --exclude 'data/' \
        --exclude '*.log' \
        "$LOCAL_DIR/" "$REMOTE_USER@$SERVER_IP:$REMOTE_DIR/"
else
    # Fallback: pscp recursive (slower, no incremental)
    "$PSCP" -pw "$PASSWORD" -batch -r \
        "$LOCAL_DIR/app" \
        "$LOCAL_DIR/main.py" \
        "$LOCAL_DIR/requirements.txt" \
        "$REMOTE_USER@$SERVER_IP:$REMOTE_DIR/"
fi

echo ""
echo "[3/5] Syncing .env..."
"$PSCP" -pw "$PASSWORD" -batch "$LOCAL_DIR/.env" "$REMOTE_USER@$SERVER_IP:$REMOTE_DIR/.env"

echo ""
echo "[4/5] Running server setup (install Python, venv, systemd)..."
$SSH "bash $REMOTE_DIR/deploy/local_1_setup_server.sh"

echo ""
echo "[5/5] Starting lqmtf service..."
$SSH "systemctl daemon-reload && systemctl enable lqmtf && systemctl restart lqmtf && sleep 2 && systemctl status lqmtf --no-pager"

echo ""
echo "============================================"
echo "  Deployment complete!"
echo "  View logs: plink -pw $PASSWORD -ssh $REMOTE_USER@$SERVER_IP journalctl -u lqmtf -f"
echo "============================================"
