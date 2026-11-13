param(
    [Parameter(Mandatory = $true)][string]$Script,
    [string]$GuestPath = "",
    [int]$Retries = 8,
    [int]$DelaySeconds = 5
)

$vbm = 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
$vm = 'Astra'
$user = 'helpdesk'
$pass = 'it~support'

if (-not $GuestPath) {
    $GuestPath = "/home/helpdesk/" + (Split-Path -Leaf $Script)
}

for ($attempt = 1; $attempt -le $Retries; $attempt++) {
    $copy = & $vbm guestcontrol $vm copyto --username $user --password $pass $Script $GuestPath 2>&1 | Out-String
    if ($copy -match 'not ready|VBOX_E_IPRT_ERROR|GuestSessionWrap') {
        Start-Sleep -Seconds $DelaySeconds
        continue
    }
    $output = & $vbm guestcontrol $vm run --exe "/bin/sh" --username $user --password $pass `
        --wait-stdout --wait-stderr -- $GuestPath 2>&1 | Out-String
    if ($output -match 'not ready|VBOX_E_IPRT_ERROR|GuestSessionWrap') {
        Start-Sleep -Seconds $DelaySeconds
        continue
    }
    Write-Output $output
    exit 0
}
Write-Output "GUEST_SCRIPT_FAILED: $output"
exit 1
