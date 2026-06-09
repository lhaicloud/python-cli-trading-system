import subprocess
from pathlib import Path

KEY = Path("C:/Users/user/.ssh/id_ed25519")
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-i", KEY.as_posix()]
cmd = ["ssh.exe"] + SSH_OPTS + ["root@192.168.250.212", "echo ok"]
print("cmd:", cmd)
r = subprocess.run(cmd, capture_output=True, text=True)
print("rc:", r.returncode)
print("stdout:", repr(r.stdout))
print("stderr:", repr(r.stderr))
