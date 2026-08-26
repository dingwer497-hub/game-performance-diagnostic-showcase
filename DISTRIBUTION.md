# Windows 便携版构建与分发

## 使用者体验

发行物为 `game-diagnostic-demo-windows-x64.zip`。测试人员只需完整解压后运行 `game-diagnostic-demo.exe`，不需要安装 Python、psutil、PresentMon 或 Chart.js。

首次启动会检查 Windows 64 位环境、内置组件完整性、ETW 权限、数据目录写入权限和《黑神话：悟空》默认日志。会话保存到 `%LOCALAPPDATA%\GamePerformanceLogTrace\sessions`，因此发行包可放在只读或受保护目录。包内提供桌面快捷方式创建脚本和简体中文使用说明。

## 构建

构建环境需要完整的官方 Python 3.12 x64（包含 Tcl/Tk）。

```powershell
.<path-to-python> -m pip install -r requirements-dev.txt -r requirements-build.txt
.\scripts\build-portable.ps1 -PythonPath '<path-to-python.exe>'
```

构建脚本先执行自动测试，再显式打包 Python、Tkinter、Tcl/Tk、psutil、PresentMon 2.5.1 和 Chart.js 4.5.1，产物位于 `dist/`。

## 中文路径兼容

Tcl/Tk 8.6 在部分 Windows 中文路径中会错误解析资源目录。发行入口会在首次运行时将 Tcl/Tk 脚本优先缓存到 `%LOCALAPPDATA%\GamePerformanceLogTrace` 的英文路径；若用户名路径也包含非英文字符，再降级到 `%PUBLIC%\GamePerformanceLogTrace`，然后启动 GUI。已在包含中文字符的解压路径中完成实际启动验证。

## 已知限制

- 发行 EXE 尚未使用商业代码签名，Windows SmartScreen 可能显示未知发布者。
- 当前是便携 ZIP，不是 MSI/MSIX 安装器，不会自动修改系统组权限。
- PresentMon ETW 需要管理员令牌或已生效的 Performance Log Users 组权限。
- 已完成 Windows 10 + NVIDIA 实机验证；Windows 11、AMD 和 Intel GPU 仍需独立硬件回归。
- 外置浮窗优先面向无边框全屏/窗口模式，独占全屏覆盖尚未验证。
