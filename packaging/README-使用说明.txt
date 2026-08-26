性能与日志追溯工具（Windows 便携版）
========================================

一、系统要求
- Windows 10/11 64 位
- 《黑神话：悟空》零售版，进程名 b1-Win64-Shipping.exe
- 建议使用无边框全屏或窗口模式

二、启动
1. 解压整个文件夹，不要只单独复制 EXE。
2. 先启动游戏，再双击 game-diagnostic-demo.exe。
3. 首次启动会显示兼容性检查。
4. 游戏中按 Ctrl+Shift+F10 打开缺陷反馈。

三、权限
若帧数据显示 UNAVAILABLE：
- 右键使用管理员身份运行；或
- 请 IT 将账户加入 Windows“Performance Log Users”组，然后注销并重新登录。

四、数据位置
%LOCALAPPDATA%\GamePerformanceLogTrace\sessions

五、显卡兼容
- 主采集链路基于 Windows 与 PresentMon，设计上兼容 NVIDIA / AMD / Intel；目前已完成 NVIDIA 实机验证，AMD / Intel 仍需对应硬件回归。
- NVIDIA：额外显示整卡温度、功耗和频率。
- AMD / Intel 缺少上述补充值不影响主采集链路。

六、快捷方式
双击“创建桌面快捷方式.cmd”可在当前用户桌面创建入口。
