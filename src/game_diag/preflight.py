from __future__ import annotations

import ctypes
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import AppConfig


@dataclass(frozen=True, slots=True)
class PreflightItem:
    key: str
    level: str
    title: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _has_presentmon_permission() -> bool:
    if os.name != "nt":
        return False
    try:
        if bool(ctypes.windll.shell32.IsUserAnAdmin()):  # type: ignore[attr-defined]
            return True
    except (AttributeError, OSError):
        pass
    try:
        completed = subprocess.run(
            ["whoami", "/groups", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return completed.returncode == 0 and "S-1-5-32-559" in completed.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def run_preflight(config: AppConfig) -> list[PreflightItem]:
    items: list[PreflightItem] = []
    windows_x64 = os.name == "nt" and platform.machine().casefold() in {"amd64", "x86_64"}
    items.append(
        PreflightItem(
            "windows_x64",
            "ok" if windows_x64 else "error",
            "Windows 64 位环境",
            f"{platform.system()} {platform.release()} / {platform.machine()}",
        )
    )
    presentmon_ok = config.presentmon_path.is_file()
    items.append(
        PreflightItem(
            "presentmon",
            "ok" if presentmon_ok else "error",
            "PresentMon 帧数采集组件",
            str(config.presentmon_path) if presentmon_ok else f"缺少文件：{config.presentmon_path}",
        )
    )
    chart_ok = config.chartjs_path.is_file()
    items.append(
        PreflightItem(
            "chartjs",
            "ok" if chart_ok else "warning",
            "离线报告图表组件",
            str(config.chartjs_path) if chart_ok else "缺失时仍会生成原始证据，但报告不显示曲线",
        )
    )
    permission_ok = _has_presentmon_permission()
    items.append(
        PreflightItem(
            "etw_permission",
            "ok" if permission_ok else "warning",
            "Windows ETW 采集权限",
            "已具备管理员或 Performance Log Users 权限"
            if permission_ok
            else "可以管理员运行，或加入 Performance Log Users 组后注销并重新登录",
        )
    )
    try:
        config.sessions_dir.mkdir(parents=True, exist_ok=True)
        probe = config.sessions_dir / ".write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        storage_level, storage_detail = "ok", str(config.sessions_dir)
    except OSError as error:
        storage_level, storage_detail = "error", f"无法写入：{error}"
    items.append(PreflightItem("storage", storage_level, "本地数据目录", storage_detail))
    log_ok = config.game_log_path.is_file()
    items.append(
        PreflightItem(
            "game_log",
            "ok" if log_ok else "warning",
            "《黑神话：悟空》游戏日志",
            str(config.game_log_path) if log_ok else f"尚未找到：{config.game_log_path}；请先启动一次游戏",
        )
    )
    return items


def format_preflight(items: list[PreflightItem]) -> str:
    markers = {"ok": "[OK]", "warning": "[!]", "error": "[X]"}
    return "\n".join(f"{markers.get(item.level, '[?]')} {item.title}\n    {item.detail}" for item in items)
