# 游戏性能与日志追溯 Demo

这是一个面向 Windows 游戏测试场景的产品与工程演示：试玩者发现问题后，只需回忆并选择问题发生的大致时间段，工具便会从最近 30 分钟的滚动数据中截取对应性能指标与游戏日志，生成供 QA 和研发查看的离线证据包。

## 一个链接查看全部内容

**[打开项目展示首页](https://dingwer497-hub.github.io/game-performance-diagnostic-showcase/)**

展示首页包含：

- Windows x64 便携版 Demo 下载
- 性能与日志追溯 Demo 需求与技术设计稿
- 缺陷反馈系统 PRD
- QA / 研发诊断报告示例
- 完整源码、测试与构建脚本入口

## 快速体验

从 [GitHub Releases](https://github.com/dingwer497-hub/game-performance-diagnostic-showcase/releases/latest) 下载 `game-diagnostic-demo-windows-x64.zip`，完整解压后运行 `game-diagnostic-demo.exe`。目标电脑不需要预装 Python。

系统要求：Windows 10/11 x64；建议以无边框全屏或窗口模式运行《黑神话：悟空》。PresentMon ETW 采集需要管理员权限，或已生效的 `Performance Log Users` 组权限。

## 核心实现

- 进程外识别 `b1-Win64-Shipping.exe`，不注入、不读写游戏内存。
- 持续采集进程 CPU、内存、Windows 进程 GPU、Present FPS、帧时间与 GPU Busy。
- 增量读取游戏文本日志，并与性能指标对齐到统一 UTC 时间轴。
- 最近 30 分钟滚动保留；按用户选择区间生成离线证据包。
- QA 信息视图与研发诊断视图分别呈现反馈、曲线、日志和原始证据。
- PyInstaller 便携发行包内置 Python、Tkinter、PresentMon 与 Chart.js。

## 安全说明

公开仓库中的三份诊断报告来自本机真实采集，保留 FPS、帧时间、CPU/GPU、内存、时间序列和游戏日志事件，便于审阅实际数据质量与异常处理效果。仅移除用户名与本机绝对路径；游戏文件、构建缓存和无关个人数据不会提交到仓库。

## 验证状态

- 37 项自动化测试通过。
- Windows 10 + NVIDIA 显卡实机验证通过。
- 便携 EXE 已验证无需目标机 Python 即可启动并生成离线报告。
- Windows 11、AMD / Intel GPU 与独占全屏场景仍需独立硬件回归。

详细构建与限制见 [DISTRIBUTION.md](DISTRIBUTION.md)。
