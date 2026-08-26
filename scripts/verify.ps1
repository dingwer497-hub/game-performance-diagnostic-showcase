$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Executable = Join-Path $ProjectRoot ".venv\Scripts\game-diagnostic-cli.exe"

if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $Executable)) {
    throw "尚未完成环境安装。请先运行 .\scripts\setup.ps1。"
}

Set-Location -LiteralPath $ProjectRoot
& $Python -m pip check
& $Python -m compileall -q src
& $Python -m pytest -q
& $Executable --demo-data --headless --duration 1 --export-last 60 --sessions-dir data\verify-sessions
Write-Host "验证完成。"
