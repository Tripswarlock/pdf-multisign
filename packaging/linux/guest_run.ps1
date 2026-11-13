param(
    [Parameter(Mandatory = $true)][string]$Command,
    [int]$Retries = 12,
    [int]$DelaySeconds = 4
)

$vbm = 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
$vm = 'Astra'
$user = 'helpdesk'
$pass = 'it~support'

for ($attempt = 1; $attempt -le $Retries; $attempt++) {
    $output = & $vbm guestcontrol $vm run --exe "/bin/sh" --username $user --password $pass `
        --wait-stdout --wait-stderr -- -c $Command 2>&1 | Out-String
    if ($output -notmatch 'not ready|VBOX_E_IPRT_ERROR|GuestSessionWrap') {
        Write-Output $output
        exit 0
    }
    Start-Sleep -Seconds $DelaySeconds
}
Write-Output "GUEST_COMMAND_FAILED: $output"
exit 1
