from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    if frozen_app():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return project_root() / ".tools"


def default_sessions_dir() -> Path:
    if frozen_app():
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local_app_data / "GamePerformanceLogTrace" / "sessions"
    return project_root() / "data" / "sessions"


def default_game_log() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "b1" / "Saved" / "Logs" / "b1.log"


@dataclass(slots=True)
class AppConfig:
    process_name: str = "b1-Win64-Shipping.exe"
    process_path_contains: str = "BlackMythWukong"
    game_log_path: Path = field(default_factory=default_game_log)
    sessions_dir: Path = field(default_factory=default_sessions_dir)
    presentmon_path: Path = field(
        default_factory=lambda: resource_root() / "presentmon" / "PresentMon-2.5.1-x64.exe"
    )
    chartjs_path: Path = field(
        default_factory=lambda: (
            resource_root() / "chartjs" / "chart.umd.min.js"
            if frozen_app()
            else resource_root() / "chartjs" / "package" / "dist" / "chart.umd.min.js"
        )
    )
    sample_interval_sec: float = 1.0
    process_wait_interval_sec: float = 1.0
    process_exit_grace_sec: float = 5.0
    history_minutes: int = 30
    max_selection_seconds: int = 600
    min_selection_seconds: int = 10
    context_seconds: int = 15
    log_time_is_utc: bool = True
    enable_presentmon: bool = True
    enable_process_gpu: bool = True
    enable_vendor_gpu_metrics: bool = True
    vendor_gpu_interval_sec: float = 5.0
    prune_interval_sec: float = 60.0
    max_presentmon_csv_mb: int = 512
    hotkey_modifiers: int = 0x0002 | 0x0004  # CTRL | SHIFT
    hotkey_vk: int = 0x79  # F10
    synthetic: bool = False

    def ensure_directories(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
