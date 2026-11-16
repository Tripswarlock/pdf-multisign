# Build the Windows MSI installer with WiX Toolset 3.14.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\windows\build_msi.ps1
param(
    [string]$Version = "1.1.0",
    [string]$OutDir = "dist"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$wix = Join-Path $root "tools\wix"
$candle = Join-Path $wix "candle.exe"
$light = Join-Path $wix "light.exe"
if (-not (Test-Path $candle) -or -not (Test-Path $light)) {
    throw "WiX не найден в $wix. Скачайте wix314-binaries.zip в wheels\tools и распакуйте в tools\wix."
}

$exe = Join-Path $root "dist\PDF-Multisign.exe"
if (-not (Test-Path $exe)) {
    Write-Host "EXE не найден, собираю..."
    & (Join-Path $root "packaging\windows\build_exe.ps1") -Version $Version -OutDir $OutDir
}

$buildDir = Join-Path $root "build\msi"
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
$tempDir = Join-Path $buildDir "tmp"
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
# light.exe создаёт временные файлы через %TEMP%; системный каталог может быть
# недоступен по правам, поэтому используем локальный.
$env:TEMP = $tempDir
$env:TMP = $tempDir
$wxs = Join-Path $root "packaging\windows\installer.wxs"
$wixobj = Join-Path $buildDir "installer.wixobj"
$msi = Join-Path $root (Join-Path $OutDir "PDF-Multisign-$Version-win64.msi")

Push-Location (Join-Path $root "packaging\windows")
try {
    & $candle -nologo -arch x64 -out $wixobj "installer.wxs"
    if ($LASTEXITCODE -ne 0) { throw "candle завершился с ошибкой $LASTEXITCODE" }

    # ICE-проверки требуют полноценного Windows Installer в интерактивной сессии
    # и падают в сборочном окружении (0x643). Структуру пакета проверяем отдельно
    # административной установкой msiexec /a.
    & $light -nologo -ext WixUIExtension -cultures:ru-RU -sval -out $msi $wixobj
    if ($LASTEXITCODE -ne 0) { throw "light завершился с ошибкой $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$size = [math]::Round((Get-Item $msi).Length / 1MB, 1)
Write-Host "собран $msi ($size МБ)"



