param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not $PythonPath) {
    $Launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($Launcher) {
        $PythonPath = (& py -3.12 -c "import sys; print(sys.executable)")
    } else {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($PythonCommand) {
            $PythonPath = $PythonCommand.Source
        }
    }
}

if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "未找到 Python 3.12。请安装 Python 3.12，或使用 -PythonPath 传入 python.exe 的完整路径。"
}

$Version = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($Version -ne "3.12") {
    throw "需要 Python 3.12，当前为 $Version。"
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & $PythonPath -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
& ".\.venv\Scripts\python.exe" -m pip install --no-deps --no-build-isolation -e .
& ".\.venv\Scripts\python.exe" -m pip check

Write-Host "环境安装完成。"
Write-Host "GUI 真实采集：.\scripts\run-real.ps1"
Write-Host "GUI 合成演示：.\scripts\run-synthetic-demo.ps1"
Write-Host "命令行验收：.\scripts\verify.ps1"
