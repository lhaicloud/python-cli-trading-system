# Deploying LQ-MTF to Google Cloud (Free Tier)

## What you get
GCP Always Free — e2-micro VM: 2 vCPU (shared), 1 GB RAM, 30 GB disk.
Free forever in us-east1, us-west1, or us-central1.
Enough for the live watcher, SQLite DB, and ML models.

---

## PART 1 — Create GCP account and VM (browser, ~15 min)

### 1.1 Create a Google Cloud account
1. Go to **https://cloud.google.com** → click **Get started for free**
2. Sign in with your Google account (lhaicloud123@gmail.com)
3. Enter billing info (credit card required for identity — **$300 free credit** for 90 days, then free tier continues automatically)
4. Create a new project: **Menu → IAM & Admin → Create Project** → name it `lqmtf`

### 1.2 Create an e2-micro VM
1. **Compute Engine → VM instances → Create instance**
2. **Name**: `lqmtf-server`
3. **Region**: `us-east1` (or `us-west1` / `us-central1`) — must be one of these for Always Free
4. **Zone**: any zone within that region (e.g. `us-east1-b`)
5. **Machine configuration**:
   - Series: **E2**
   - Machine type: **e2-micro** (2 vCPU, 1 GB RAM) ← Always Free
6. **Boot disk** → click *Change*:
   - OS: **Ubuntu 22.04 LTS**
   - Size: **30 GB** (free allowance)
7. **Firewall**: check **Allow HTTP traffic** (needed for any outbound API calls)
8. Click **Create**. Wait ~1 min for status = green checkmark.
9. Copy the **External IP address** (e.g. `34.138.100.50`)

### 1.3 Set up SSH access
GCP generates SSH keys automatically when you use the browser SSH button.
For rsync/SCP from Windows, set up a key:

```bash
# In Git Bash on Windows — generate a key if you don't have one:
ssh-keygen -t ed25519 -C "lqmtf-gcp" -f ~/.ssh/gcp_key
# Press Enter twice for no passphrase

# Copy the public key content:
cat ~/.ssh/gcp_key.pub
```

Then in GCP Console:
1. **Compute Engine → VM instances → click `lqmtf-server` → Edit**
2. Scroll to **SSH Keys** → **Add item**
3. Paste the public key content → **Save**

### 1.4 Test SSH connection (Git Bash)
```bash
ssh -i ~/.ssh/gcp_key lhaicloud@34.138.100.50   # replace IP; username = your Google account prefix
# You should see: lhaicloud@lqmtf-server:~$
```

> **Note on username**: GCP sets your Linux username from your Google account.
> Run `whoami` in the browser SSH to confirm it (often first name or email prefix).

---

## PART 2 — First-time server setup (run on server)

```bash
# SSH in first:
ssh -i ~/.ssh/gcp_key lhaicloud@34.138.100.50

# Create the app directory:
mkdir -p ~/lqmtf/deploy
```

Copy the setup script to the server:
```bash
# From Git Bash on Windows:
scp -i ~/.ssh/gcp_key deploy/gcp/1_setup_server_gcp.sh lhaicloud@34.138.100.50:~/lqmtf/deploy/
```

Back on the server, run it:
```bash
bash ~/lqmtf/deploy/1_setup_server_gcp.sh
```

This installs Python 3.11, creates the virtualenv, and registers the systemd service.

---

## PART 3 — Push code + data (run locally in Git Bash)

**Stop the local watcher first** (so the SQLite WAL is flushed cleanly):
```bash
# In Git Bash, from the project root:
bash deploy/gcp/2_push_gcp.sh 34.138.100.50 ~/.ssh/gcp_key lhaicloud
```

This syncs:
- All Python source code
- ML models (~136 MB)
- SQLite database (~1.7 GB)  ← takes a few minutes
- Your `.env` (Telegram token, etc.)

---

## PART 4 — Install dependencies on server

```bash
# SSH into server:
ssh -i ~/.ssh/gcp_key lhaicloud@34.138.100.50

# Install Python packages and test:
bash ~/lqmtf/deploy/3_install_deps.sh
```

---

## PART 5 — Start the watcher

```bash
# Start the service:
sudo systemctl start lqmtf

# Check status:
sudo systemctl status lqmtf

# Follow live logs:
journalctl -u lqmtf -f
```

The watcher will:
1. Sync the universe from Binance top-50 (backfills any missing coins)
2. Auto-select the top 4 symbols by MTF conviction
3. Start monitoring every 30-minute candle close
4. Send Telegram heartbeats every 8 cycles (~4 hours)
5. Open/close paper trades automatically

---

## PART 6 — Ongoing maintenance

### Update code after local changes
```bash
# Git Bash on Windows — push code only (fast, no DB):
SERVER="lhaicloud@34.138.100.50"
rsync -avz --progress -e "ssh -i ~/.ssh/gcp_key" \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude 'venv/' \
  --exclude '.git/' --exclude 'data/' --exclude '.env' \
  . "$SERVER:~/lqmtf/"

# Restart the service to pick up the new code:
ssh -i ~/.ssh/gcp_key $SERVER "sudo systemctl restart lqmtf"
```

### View watcher output
```bash
ssh -i ~/.ssh/gcp_key lhaicloud@34.138.100.50 "journalctl -u lqmtf -f --no-pager"
```

### Run a backtest on the server
```bash
ssh -i ~/.ssh/gcp_key lhaicloud@34.138.100.50
cd ~/lqmtf && source venv/bin/activate
python quick_backtest.py > data/remote_bt.txt 2>&1 &
tail -f data/remote_bt.txt
```

### Pull updated DB back to Windows (after server has run a while)
```bash
# Git Bash on Windows — sync DB back:
rsync -avz --progress -e "ssh -i ~/.ssh/gcp_key" \
  lhaicloud@34.138.100.50:~/lqmtf/data/db/ \
  "D:/AI Projects/python-cli-trading-system/data/db/"
```

### Service commands cheatsheet
```bash
sudo systemctl start   lqmtf   # start
sudo systemctl stop    lqmtf   # stop
sudo systemctl restart lqmtf   # restart (picks up code/config changes)
sudo systemctl status  lqmtf   # one-line status
journalctl -u lqmtf -f         # tail live logs
journalctl -u lqmtf --since "1 hour ago"   # last hour
```

---

## Architecture on server

```
/home/lhaicloud/lqmtf/
├── main.py              # CLI entry point
├── app/                 # all Python source
├── data/
│   ├── db/lqmtf.db     # SQLite (candles + trades + models metadata)
│   ├── models/*.joblib  # ML model files
│   └── lqmtf.log       # Python logger output
├── deploy/              # these deployment files
├── venv/                # Python virtualenv (not rsynced)
└── .env                 # secrets (not in git)

systemd unit:  /etc/systemd/system/lqmtf.service
logs:          journalctl -u lqmtf
```

---

## RAM Warning (1 GB limit)

The e2-micro has only 1 GB RAM. Your ML models + pandas dataframes + scikit-learn
can push close to the limit. Add a 1 GB swap file as a safety net:

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## Troubleshooting

**Service fails to start**
```bash
journalctl -u lqmtf -n 50 --no-pager
```
Common causes:
- `.env` not created or missing `TELEGRAM_BOT_TOKEN`
- DB not yet synced (`data/db/lqmtf.db` missing)
- Python import error (run `bash deploy/3_install_deps.sh` again)
- OOM killed (add swap — see RAM Warning above)

**Out of disk space**
```bash
df -h          # check disk usage
du -sh ~/lqmtf/data/lqmtf.log    # log file (can grow large)
# Truncate log if needed:
truncate -s 0 ~/lqmtf/data/lqmtf.log
```

**Check if OOM killed**
```bash
dmesg | grep -i "killed process"
journalctl -u lqmtf --since "1 hour ago" | grep -i "killed\|memory\|oom"
```

**Wrong username**
If SSH says "Permission denied", check your Linux username:
Open browser SSH in GCP Console → run `whoami` → use that name in all commands above.
