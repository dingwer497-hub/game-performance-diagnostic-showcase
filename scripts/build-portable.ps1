param(
    [switch]$SkipTests,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $ProjectRoot ".venv\Scripts\python.exe" }
$PythonBase = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
$PresentMon = Join-Path $ProjectRoot ".tools\presentmon\PresentMon-2.5.1-x64.exe"
$ChartJs = Join-Path $ProjectRoot ".tools\chartjs\package\dist\chart.umd.min.js"
$TclData = Join-Path $PythonBase "tcl\tcl8.6"
$TkData = Join-Path $PythonBase "tcl\tk8.6"
$TkinterPyd = Join-Path $PythonBase "DLLs\_tkinter.pyd"
$TclDll = Join-Path $PythonBase "DLLs\tcl86t.dll"
$TkDll = Join-Path $PythonBase "DLLs\tk86t.dll"
$DistRoot = Join-Path $ProjectRoot "dist"
$WorkRoot = Join-Path $ProjectRoot "tmp\pyinstaller"
$AppName = "game-diagnostic-demo"
$AppDir = Join-Path $DistRoot $AppName
$Archive = Join-Path $DistRoot "$AppName-windows-x64.zip"

foreach ($Required in @($Python, $PresentMon, $ChartJs, $TclData, $TkData, $TkinterPyd, $TclDll, $TkDll)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "缺少构建依赖：$Required"
    }
}
if (-not $SkipTests) {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "测试未通过，已停止构建。" }
}
& $Python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "未安装 PyInstaller，请先运行：.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt"
}

foreach ($Target in @($AppDir, $WorkRoot)) {
    if (Test-Path -LiteralPath $Target) {
        $Resolved = (Resolve-Path -LiteralPath $Target).Path
        if (-not $Resolved.StartsWith($ProjectRoot + [IO.Path]::DirectorySeparatorChar)) {
            throw "拒绝删除项目外路径：$Resolved"
        }
        Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
}
if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name $AppName `
    --paths (Join-Path $ProjectRoot "src") `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $WorkRoot `
    --additional-hooks-dir (Join-Path $ProjectRoot "packaging\pyinstaller-hooks") `
    --hidden-import tkinter `
    --hidden-import tkinter.messagebox `
    --add-binary "$TkinterPyd;." `
    --add-binary "$TclDll;." `
    --add-binary "$TkDll;." `
    --add-data "$TclData;_tcl_data" `
    --add-data "$TkData;_tk_data" `
    --add-binary "$PresentMon;presentmon" `
    --add-data "$ChartJs;chartjs" `
    (Join-Path $ProjectRoot "packaging\launcher.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败。" }

Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\README-使用说明.txt") -Destination $AppDir
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\创建桌面快捷方式.ps1") -Destination $AppDir
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\创建桌面快捷方式.cmd") -Destination $AppDir
Copy-Item -LiteralPath (Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md") -Destination $AppDir

Compress-Archive -Path $AppDir -DestinationPath $Archive -CompressionLevel Optimal
Write-Host "便携版已生成：$Archive"
