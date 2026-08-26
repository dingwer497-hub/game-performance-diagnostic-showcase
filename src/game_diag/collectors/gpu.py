from __future__ import annotations

import csv
import ctypes
import os
import subprocess
import time
from ctypes import wintypes


class _ValueUnion(ctypes.Union):
    _fields_ = [("longValue", wintypes.LONG), ("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong)]


class _FormattedValue(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _ValueUnion)]


class _FormattedItem(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _FormattedValue)]


class ProcessGpuSampler:
    """Windows PDH GPU Engine sampler; process scope uses the busiest engine."""

    PDH_FMT_DOUBLE = 0x00000200
    PDH_MORE_DATA = 0x800007D2
    PDH_NO_DATA = 0x800007D5
    ERROR_SUCCESS = 0

    def __init__(self, pid: int, retry_interval_sec: float = 2.0) -> None:
        self.pid = pid
        self.retry_interval_sec = retry_interval_sec
        self._next_retry_at = 0.0
        self.reason: str | None = None
        self.available = False
        self.query = wintypes.HANDLE()
        self.counter = wintypes.HANDLE()
        if os.name != "nt":
            self.reason = "Windows GPU Engine 仅适用于 Windows"
            return
        self.pdh = ctypes.WinDLL("pdh", use_last_error=True)
        self._configure_api()
        self._initialize()

    @staticmethod
    def _status_code(status: int) -> int:
        return status & 0xFFFFFFFF

    def _initialize(self) -> bool:
        self.close()
        try:
            status = self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query))
            if self._status_code(status) != self.ERROR_SUCCESS:
                raise OSError(f"PdhOpenQueryW=0x{self._status_code(status):08X}")
            path = rf"\GPU Engine(*pid_{self.pid}*)\Utilization Percentage"
            status = self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(self.counter))
            if self._status_code(status) != self.ERROR_SUCCESS:
                raise OSError(f"PdhAddEnglishCounterW=0x{self._status_code(status):08X}")
            status = self.pdh.PdhCollectQueryData(self.query)
            status_code = self._status_code(status)
            if status_code != self.ERROR_SUCCESS:
                detail = "，GPU Engine 实例尚未就绪，将自动重试" if status_code == self.PDH_NO_DATA else ""
                raise OSError(f"PdhCollectQueryData=0x{status_code:08X}{detail}")
            self.available = True
            self.reason = None
            self._next_retry_at = 0.0
            return True
        except Exception as error:
            self.reason = f"{type(error).__name__}: {error}"
            self.close()
            self._next_retry_at = time.monotonic() + self.retry_interval_sec
            return False

    def _ensure_available(self) -> bool:
        if self.available:
            return True
        if time.monotonic() < self._next_retry_at:
            return False
        return self._initialize()

    def _configure_api(self) -> None:
        self.pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self.pdh.PdhOpenQueryW.restype = wintypes.LONG
        self.pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self.pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
        self.pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self.pdh.PdhCollectQueryData.restype = wintypes.LONG
        self.pdh.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
        self.pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self.pdh.PdhCloseQuery.restype = wintypes.LONG

    def sample(self) -> tuple[float | None, str | None]:
        if not self._ensure_available():
            return None, self.reason or "GPU Engine 计数器不可用"
        try:
            status = self.pdh.PdhCollectQueryData(self.query)
            status_code = self._status_code(status)
            if status_code != self.ERROR_SUCCESS:
                reason = f"PdhCollectQueryData=0x{status_code:08X}"
                if status_code == self.PDH_NO_DATA:
                    self.reason = reason + "，将自动重试"
                    self.close()
                    self._next_retry_at = time.monotonic() + self.retry_interval_sec
                    return None, self.reason
                return None, reason
            buffer_size = wintypes.DWORD(0)
            item_count = wintypes.DWORD(0)
            status = self.pdh.PdhGetFormattedCounterArrayW(
                self.counter,
                self.PDH_FMT_DOUBLE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                None,
            )
            status_code = self._status_code(status)
            if status_code not in (self.PDH_MORE_DATA, self.ERROR_SUCCESS) or buffer_size.value == 0:
                return None, "目标 PID 暂无 GPU Engine 实例"
            buffer = ctypes.create_string_buffer(buffer_size.value)
            status = self.pdh.PdhGetFormattedCounterArrayW(
                self.counter,
                self.PDH_FMT_DOUBLE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                buffer,
            )
            if status != self.ERROR_SUCCESS:
                return None, f"PdhGetFormattedCounterArrayW=0x{status:08X}"
            items = ctypes.cast(buffer, ctypes.POINTER(_FormattedItem))
            values = [items[index].FmtValue.doubleValue for index in range(item_count.value) if items[index].FmtValue.CStatus == 0]
            if not values:
                return None, "目标 PID 的 GPU Engine 样本无效"
            return min(100.0, max(0.0, max(values))), None
        except Exception as error:
            return None, f"{type(error).__name__}: {error}"

    def close(self) -> None:
        if getattr(self, "query", None) and getattr(self.query, "value", None):
            try:
                self.pdh.PdhCloseQuery(self.query)
            except Exception:
                pass
            self.query = wintypes.HANDLE()
        self.available = False


class NvidiaDeviceSampler:
    """Optional device-scope supplement; never substitutes process GPU metrics."""

    METRICS = (
        ("device_gpu_util_pct", "percent"),
        ("device_vram_used_mb", "MiB"),
        ("device_gpu_temperature_c", "celsius"),
        ("device_gpu_power_w", "watt"),
        ("device_gpu_clock_mhz", "MHz"),
    )

    def sample(self) -> tuple[dict[str, float], str | None]:
        command = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.gr",
            "--format=csv,noheader,nounits",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            row = next(csv.reader([completed.stdout.splitlines()[0]]))
            values = [float(value.strip()) for value in row]
            return {name: value for (name, _unit), value in zip(self.METRICS, values)}, None
        except (OSError, subprocess.SubprocessError, ValueError, IndexError, StopIteration) as error:
            return {}, f"{type(error).__name__}: {error}"
