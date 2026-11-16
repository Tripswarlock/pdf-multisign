# Build the portable Windows executable with PyInstaller.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\windows\build_exe.ps1
param(
    [string]$Version = "1.1.0",
    [string]$OutDir = "dist"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$vendor = Join-Path $root "vendor"
$tools = Join-Path $root "tools\pyinstaller"
if (-not (Test-Path $tools)) {
    Write-Host "PyInstaller не найден, скачиваю..."
    python packaging\windows\fetch_build_tools.py
}

$env:PYTHONPATH = @(
    $root,
    (Join-Path $vendor "win32"),
    (Join-Path $vendor "win32\lib"),
    $vendor,
    $tools
) -join ";"
$env:PATH = (Join-Path $vendor "pywin32_system32") + ";" + $env:PATH
$env:PYTHONIOENCODING = "utf-8"

$icon = Join-Path $root "resources\pdf-multisign.ico"
$png = Join-Path $root "resources\pdf-multisign.png"
$buildRoot = Join-Path $root "build\pyinstaller"
$target = Join-Path $root (Join-Path $OutDir "PDF-Multisign.exe")

python -m PyInstaller `
    --noconfirm --clean --onefile --windowed `
    --name "PDF-Multisign" `
    --icon $icon `
    --add-data "$png;resources" `
    --collect-submodules win32com `
    --collect-submodules pythoncom `
    --hidden-import PySide6.QtSvg `
    --hidden-import pymupdf `
    --hidden-import fitz `
    --hidden-import asn1crypto `
    --hidden-import gostcrypto `
    --paths $root `
    --distpath (Join-Path $root $OutDir) `
    --workpath $buildRoot `
    --specpath $buildRoot `
    packaging\windows\entry.py

if (-not (Test-Path $target)) {
    Write-Error "сборка не создала $target"
}
$size = [math]::Round((Get-Item $target).Length / 1MB, 1)
Write-Host "собран $target ($size МБ)"



