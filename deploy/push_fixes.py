"""
Deploy changed source files to the local server and restart the service.
Uses Windows OpenSSH (scp.exe / ssh.exe) with key-based auth.

Usage: python deploy/push_fixes.py
"""
import subprocess
import sys
from pathlib import Path

HOST   = "root@192.168.250.212"
KEY    = "C:/Users/user/.ssh/lqmtf_server"
LOCAL  = Path(__file__).parent.parent
REMOTE = "/root/lqmtf"

FILES = [
    "app/ta/signals.py",
    "app/backtesting/simulator.py",
    "app/live/watcher.py",
]

SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-i", KEY]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    out = r.stdout.decode("utf-8", errors="replace")
    err = r.stderr.decode("utf-8", errors="replace")
    if r.returncode != 0:
        print(f"ERROR: {err.strip()}", file=sys.stderr)
        sys.exit(r.returncode)
    return out


def main():
    print("Uploading files...")
    for rel in FILES:
        local  = str(LOCAL / rel).replace("\\", "/")
        remote = f"{HOST}:{REMOTE}/{rel}"
        print(f"  {rel} ...", end=" ", flush=True)
        run(["scp.exe", *SSH_OPTS, local, remote])
        print("OK")

    print("\nRestarting service...")
    out = run([
        "ssh.exe", *SSH_OPTS, HOST,
        "systemctl restart lqmtf && sleep 2 && systemctl status lqmtf --no-pager | head -15"
    ])
    print(out)


if __name__ == "__main__":
    main()
