$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Executable = Join-Path $ProjectRoot ".venv\Scripts\game-diagnostic-demo.exe"

if (-not (Test-Path -LiteralPath $Executable)) {
    throw "尚未完成环境安装。请先运行 .\scripts\setup.ps1。"
}

Set-Location -LiteralPath $ProjectRoot
& $Executable
