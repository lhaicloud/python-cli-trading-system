# Deploying LQ-MTF to Oracle Cloud Free Tier

## What you get
Oracle Always Free — Ampere A1 ARM64 VM:  4 OCPU, 24 GB RAM, 50 GB disk.
More than enough for the live watcher, SQLite DB (1.7 GB), and ML models.

---

## PART 1 — Create the OCI account and VM (browser, ~15 min)

### 1.1 Create an Oracle Cloud account
1. Go to **https://cloud.oracle.com** → click **Start for free**
2. Fill in name, email, password
3. Verify your email
4. Enter phone number (SMS verify)
5. Enter a credit card (required for identity, **will not be charged** for Always Free resources)
6. Choose your **Home Region** — pick the closest one to you.  
   You cannot change this later.
7. Complete signup → wait for "Your account is ready" email (~2 min)

### 1.2 Create an Ampere A1 VM
1. Log in → **Compute → Instances → Create instance**
2. **Name**: `lqmtf-server`
3. **Image**: `Canonical Ubuntu 22.04`
4. **Shape** → click *Change shape*:
   - Processor: **Ampere**
   - Shape: **VM.Standard.A1.Flex**
   - OCPU: **4**, Memory: **24 GB**  ← these are Always Free
5. **SSH keys**: 
   - Generate a key pair → **Download private key** (`oci_key.pem`)
   - Save it to `C:\Users\<you>\.ssh\oci_key.pem`
6. **Boot volume**: keep 50 GB
7. Click **Create**. Wait ~2 min for status = **Running**.
8. Copy the **Public IP address** (e.g. `152.67.100.50`)

### 1.3 Open SSH port in the firewall (Security List)
OCI blocks all inbound traffic by default. Port 22 is usually already open.
Verify: Networking → Virtual Cloud Networks → your VCN → Security Lists → Default.
Confirm **port 22 TCP** ingress from `0.0.0.0/0` exists. If not, add it.

### 1.4 Fix key permissions and test SSH (Git Bash)
```bash
# In Git Bash on Windows:
chmod 600 ~/.ssh/oci_key.pem
ssh -i ~/.ssh/oci_key.pem ubuntu@152.67.100.50    # replace with your IP
# You should see:  ubuntu@lqmtf-server:~$
```

---

## PART 2 — First-time server setup (run on server)

```bash
# SSH in first:
ssh -i ~/.ssh/oci_key.pem ubuntu@152.67.100.50

# Create the app directory:
mkdir -p ~/lqmtf/deploy
```

Copy the setup script to the server:
```bash
# From Git Bash on Windows:
scp -i ~/.ssh/oci_key.pem deploy/1_setup_server.sh ubuntu@152.67.100.50:~/lqmtf/deploy/
```

Back on the server, run it:
```bash
bash ~/lqmtf/deploy/1_setup_server.sh
```

This installs Python 3.11, creates the virtualenv, and registers the systemd service.

---

## PART 3 — Push code + data (run locally in Git Bash)

**Stop the local watcher first** (so the SQLite WAL is flushed cleanly):
```bash
# In Git Bash, from the project root:
bash deploy/2_push.sh 152.67.100.50 ~/.ssh/oci_key.pem
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
ssh -i ~/.ssh/oci_key.pem ubuntu@152.67.100.50

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
SERVER="ubuntu@152.67.100.50"
rsync -avz --progress -e "ssh -i ~/.ssh/oci_key.pem" \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude 'venv/' \
  --exclude '.git/' --exclude 'data/' --exclude '.env' \
  . "$SERVER:~/lqmtf/"

# Restart the service to pick up the new code:
ssh -i ~/.ssh/oci_key.pem $SERVER "sudo systemctl restart lqmtf"
```

### View watcher output
```bash
ssh -i ~/.ssh/oci_key.pem ubuntu@152.67.100.50 "journalctl -u lqmtf -f --no-pager"
```

### Run a backtest on the server
```bash
ssh -i ~/.ssh/oci_key.pem ubuntu@152.67.100.50
cd ~/lqmtf && source venv/bin/activate
python quick_backtest.py > data/remote_bt.txt 2>&1 &
tail -f data/remote_bt.txt
```

### Pull updated DB back to Windows (after server has run a while)
```bash
# Git Bash on Windows — sync DB back:
rsync -avz --progress -e "ssh -i ~/.ssh/oci_key.pem" \
  ubuntu@152.67.100.50:~/lqmtf/data/db/ \
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
/home/ubuntu/lqmtf/
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

## Troubleshooting

**Service fails to start**
```bash
journalctl -u lqmtf -n 50 --no-pager
```
Common causes:
- `.env` not created or missing `TELEGRAM_BOT_TOKEN`
- DB not yet synced (`data/db/lqmtf.db` missing)
- Python import error (run `bash deploy/3_install_deps.sh` again)

**Out of disk space**
```bash
df -h          # check disk usage
du -sh ~/lqmtf/data/lqmtf.log    # log file (can grow large)
# Truncate log if needed:
truncate -s 0 ~/lqmtf/data/lqmtf.log
```

**Port 22 blocked by OCI**
Add ingress rule in: OCI console → Networking → VCN → Security Lists → Default Security List → Add Ingress Rule:
- Source: `0.0.0.0/0`, Protocol: TCP, Port: 22
