$ErrorActionPreference = "Stop"
$Executable = Join-Path $PSScriptRoot "game-diagnostic-demo.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "未找到 game-diagnostic-demo.exe，请在完整解压的发行目录中运行。"
}
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "性能与日志追溯工具.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Executable
$Shortcut.WorkingDirectory = $PSScriptRoot
$Shortcut.Description = "《黑神话：悟空》性能与日志追溯工具"
$Shortcut.Save()
Write-Host "已创建：$ShortcutPath"
