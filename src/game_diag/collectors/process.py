from __future__ import annotations

import os
from ctypes import WinDLL, byref, wintypes
from dataclasses import dataclass
from pathlib import Path

import psutil

from ..models import MetricRecord


@dataclass(slots=True)
class ProcessMatch:
    pid: int
    name: str
    executable: str | None
    create_time: float
    rss: int


class ProcessFinder:
    def __init__(self, process_name: str, path_contains: str = "") -> None:
        self.process_name = process_name.casefold()
        self.path_contains = path_contains.casefold()

    def find(self) -> ProcessMatch | None:
        matches: list[ProcessMatch] = []
        for process in psutil.process_iter(["pid", "name", "exe", "create_time", "memory_info"]):
            try:
                info = process.info
                if str(info.get("name") or "").casefold() != self.process_name:
                    continue
                executable = info.get("exe")
                if self.path_contains and executable and self.path_contains not in str(executable).casefold():
                    continue
                memory_info = info.get("memory_info")
                matches.append(
                    ProcessMatch(
                        pid=int(info["pid"]),
                        name=str(info.get("name") or self.process_name),
                        executable=str(executable) if executable else None,
                        create_time=float(info.get("create_time") or 0),
                        rss=int(getattr(memory_info, "rss", 0)),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        if not matches:
            return None
        # The real renderer is expected to be unique. If duplicate instances exist,
        # the process with the largest resident set is the safest deterministic pick.
        return max(matches, key=lambda item: (item.rss, item.create_time, item.pid))


class ProcessSampler:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.process = psutil.Process(pid)
        self.logical_cpu_count = max(1, psutil.cpu_count(logical=True) or 1)
        self.process.cpu_percent(interval=None)

    def is_running(self) -> bool:
        try:
            return self.process.is_running() and self.process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def sample(self, session_id: str, ts_utc_ms: int, elapsed_ms: int) -> list[MetricRecord]:
        try:
            raw_cpu = float(self.process.cpu_percent(interval=None))
            normalized_cpu = min(100.0, max(0.0, raw_cpu / self.logical_cpu_count))
            memory_mb = float(self.process.memory_info().rss) / (1024 * 1024)
            foreground = process_is_foreground(self.pid)
            return [
                MetricRecord(
                    session_id=session_id,
                    ts_utc_ms=ts_utc_ms,
                    elapsed_ms=elapsed_ms,
                    source="psutil",
                    metric="process_cpu_pct_raw",
                    value=raw_cpu,
                    unit="percent",
                    scope="process",
                ),
                MetricRecord(
                    session_id=session_id,
                    ts_utc_ms=ts_utc_ms,
                    elapsed_ms=elapsed_ms,
                    source="psutil",
                    metric="process_cpu_pct",
                    value=normalized_cpu,
                    unit="percent",
                    scope="process",
                ),
                MetricRecord(
                    session_id=session_id,
                    ts_utc_ms=ts_utc_ms,
                    elapsed_ms=elapsed_ms,
                    source="psutil",
                    metric="process_memory_mb",
                    value=memory_mb,
                    unit="MiB",
                    scope="process",
                ),
                MetricRecord(
                    session_id=session_id,
                    ts_utc_ms=ts_utc_ms,
                    elapsed_ms=elapsed_ms,
                    source="win32_foreground",
                    metric="game_foreground",
                    value=None if foreground is None else float(foreground),
                    unit="boolean",
                    status="ok" if foreground is not None else "unavailable",
                    reason=None if foreground is not None else "无法读取 Windows 前台窗口",
                    scope="process",
                ),
            ]
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as error:
            reason = f"{type(error).__name__}: {error}"
            return [
                MetricRecord(
                    session_id=session_id,
                    ts_utc_ms=ts_utc_ms,
                    elapsed_ms=elapsed_ms,
                    source="psutil",
                    metric=metric,
                    value=None,
                    unit=unit,
                    status="unavailable",
                    reason=reason,
                    scope="process",
                )
                for metric, unit in (
                    ("process_cpu_pct_raw", "percent"),
                    ("process_cpu_pct", "percent"),
                    ("process_memory_mb", "MiB"),
                    ("game_foreground", "boolean"),
                )
            ]


def process_is_foreground(pid: int) -> bool | None:
    """Return whether the foreground top-level window belongs to *pid*."""
    if os.name != "nt":
        return None
    try:
        user32 = WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, wintypes.LPDWORD)
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        foreground_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(foreground_pid))
        return foreground_pid.value == pid
    except (AttributeError, OSError):
        return None


def process_environment(match: ProcessMatch) -> dict[str, object]:
    return {
        "target_executable": match.executable,
        "target_process_name": match.name,
        "target_pid": match.pid,
        "target_create_time": match.create_time,
        "host_logical_cpus": psutil.cpu_count(logical=True),
        "host_physical_cpus": psutil.cpu_count(logical=False),
        "host_memory_bytes": psutil.virtual_memory().total,
        "host_platform": os.name,
        "target_drive": Path(match.executable).drive if match.executable else None,
    }
