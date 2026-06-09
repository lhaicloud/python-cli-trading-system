#!/usr/bin/env bash
# Run this from Git Bash on your Windows machine.
# Syncs code + data to the GCP server.
#
# Usage:
#   bash deploy/gcp/2_push_gcp.sh <server-ip> [path/to/ssh-key] [remote-user]
#
# Examples:
#   bash deploy/gcp/2_push_gcp.sh 34.138.100.50
#   bash deploy/gcp/2_push_gcp.sh 34.138.100.50 ~/.ssh/gcp_key lhaicloud

set -e

SERVER_IP="${1:?Usage: $0 <server-ip> [ssh-key] [remote-user]}"
SSH_KEY="${2:-~/.ssh/gcp_key}"
REMOTE_USER="${3:-$(whoami)}"
REMOTE_DIR="/home/$REMOTE_USER/lqmtf"

# Resolve Windows Git Bash path  (D:\AI Projects\... → /d/AI Projects/...)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=accept-new"

echo "============================================"
echo "  LQ-MTF → GCP push"
echo "  From: $LOCAL_DIR"
echo "  To:   $REMOTE_USER@$SERVER_IP:$REMOTE_DIR"
echo "============================================"

# ── Step 1: Push code (fast, excludes big data) ──────────────────────────────
echo ""
echo "[1/4] Syncing code..."
rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'venv/' \
    --exclude '.git/' \
    --exclude 'data/' \
    --exclude '*.log' \
    "$LOCAL_DIR/" "$REMOTE_USER@$SERVER_IP:$REMOTE_DIR/"

# ── Step 2: Push ML models (136 MB) ──────────────────────────────────────────
echo ""
echo "[2/4] Syncing ML models (~136 MB)..."
rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    "$LOCAL_DIR/data/models/" "$REMOTE_USER@$SERVER_IP:$REMOTE_DIR/data/models/"

# ── Step 3: Push SQLite DB (~1.7 GB) ─────────────────────────────────────────
echo ""
echo "[3/4] Syncing SQLite database (~1.7 GB — may take a few minutes)..."
echo "      IMPORTANT: the local watcher should be stopped before this step"
echo "      to avoid copying a partially-written WAL file."
echo ""
rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    "$LOCAL_DIR/data/db/" "$REMOTE_USER@$SERVER_IP:$REMOTE_DIR/data/db/"

# ── Step 4: Push .env (secrets) ──────────────────────────────────────────────
echo ""
echo "[4/4] Pushing .env (Telegram token etc.)..."
rsync -avz \
    -e "ssh $SSH_OPTS" \
    "$LOCAL_DIR/.env" "$REMOTE_USER@$SERVER_IP:$REMOTE_DIR/.env"

echo ""
echo "============================================"
echo "  Push complete!"
echo ""
echo "  On the server, run:"
echo "    cd ~/lqmtf && source venv/bin/activate"
echo "    pip install -r requirements.txt        # first time only"
echo "    sudo systemctl start lqmtf"
echo "    journalctl -u lqmtf -f                 # watch logs"
echo "============================================"
