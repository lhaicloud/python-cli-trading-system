# Installs the local SSH public key on the remote server using password auth.
# Run once from PowerShell: .\deploy\install_key.ps1

$key = Get-Content "$env:USERPROFILE\.ssh\lqmtf_local.pub" -Raw
$key = $key.Trim()
$plink = "C:\Program Files\PuTTY\plink.exe"
$pscp  = "C:\Program Files\PuTTY\pscp.exe"

# Write key to a temp file, upload it, then append to authorized_keys
$tmp = "$env:TEMP\deploy_key.pub"
$key | Out-File -FilePath $tmp -Encoding ascii -NoNewline

Write-Host "Uploading public key..."
& $pscp -pw VirtualWorld10 -batch $tmp "root@192.168.250.212:/tmp/deploy_key.pub"

Write-Host "Installing key on server..."
& $plink -pw VirtualWorld10 -batch root@192.168.250.212 @'
mkdir -p /root/.ssh
cat /tmp/deploy_key.pub >> /root/.ssh/authorized_keys
sort -u /root/.ssh/authorized_keys -o /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
rm /tmp/deploy_key.pub
echo "Key installed OK"
'@

Write-Host "Testing key auth..."
& $plink -i "$env:USERPROFILE\.ssh\lqmtf_local" -batch root@192.168.250.212 "echo key-auth-works"
