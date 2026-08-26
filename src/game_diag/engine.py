from __future__ import annotations

import platform
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import psutil

from .collectors.gpu import NvidiaDeviceSampler, ProcessGpuSampler
from .collectors.log_tailer import LogTailer
from .collectors.presentmon import PresentMonAdapter, PresentMonCsvTailer
from .collectors.process import ProcessFinder, ProcessMatch, ProcessSampler, process_environment
from .config import AppConfig
from .models import MetricRecord, RuntimeSnapshot, SourceStatus
from .session import SessionStore, utc_now_ms
from .synthetic import synthetic_event, synthetic_metrics


class DiagnosticEngine:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.config.ensure_directories()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._snapshot = RuntimeSnapshot(synthetic=config.synthetic)
        self._store: SessionStore | None = None
        self._latest_store: SessionStore | None = None
        self._listeners: list[Callable[[RuntimeSnapshot], None]] = []

    @property
    def latest_store(self) -> SessionStore | None:
        return self._latest_store

    def add_listener(self, listener: Callable[[RuntimeSnapshot], None]) -> None:
        self._listeners.append(listener)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        target = self._run_synthetic if self.config.synthetic else self._run_normal
        self._thread = threading.Thread(target=target, name="diagnostic-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._thread and self._thread.is_alive() and self._store:
            self._store.close(abnormal=True, reason="采集线程未在超时内停止")

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot.copy()

    def export_range(self, selected_start_utc_ms: int, selected_end_utc_ms: int) -> Path:
        store = self._latest_store
        if store is None:
            raise RuntimeError("尚无可导出的采集会话")
        from .exporter import RangeExporter

        exporter = RangeExporter(
            store.path,
            chartjs_path=self.config.chartjs_path,
            history_minutes=self.config.history_minutes,
            min_selection_seconds=self.config.min_selection_seconds,
            max_selection_seconds=self.config.max_selection_seconds,
            context_seconds=self.config.context_seconds,
        )
        return exporter.export(selected_start_utc_ms, selected_end_utc_ms)

    def _run_normal(self) -> None:
        finder = ProcessFinder(self.config.process_name, self.config.process_path_contains)
        self._set_runtime(state="waiting", message=f"等待 {self.config.process_name}")
        while not self._stop.is_set():
            match = finder.find()
            if match is None:
                self._stop.wait(self.config.process_wait_interval_sec)
                continue
            try:
                self._capture_process(match)
            except Exception as error:
                self._set_runtime(state="error", message=f"采集会话异常：{type(error).__name__}: {error}")
                if self._store:
                    self._store.close(abnormal=True, reason=str(error))
                    self._store = None
                self._stop.wait(1.0)
            if not self._stop.is_set():
                self._set_runtime(state="waiting", pid=None, message=f"等待 {self.config.process_name}")
        self._set_runtime(state="stopped", message="工具已停止")

    def _capture_process(self, match: ProcessMatch) -> None:
        environment = self._host_environment()
        environment.update(process_environment(match))
        store = SessionStore(
            self.config.sessions_dir,
            process_name=match.name,
            pid=match.pid,
            environment=environment,
        )
        self._store = self._latest_store = store
        sampler = ProcessSampler(match.pid)
        log_tailer = LogTailer(self.config.game_log_path, self.config.log_time_is_utc, start_at_end=False)
        gpu_sampler = ProcessGpuSampler(match.pid) if self.config.enable_process_gpu else None
        vendor_sampler = NvidiaDeviceSampler() if self.config.enable_vendor_gpu_metrics else None
        presentmon: PresentMonAdapter | None = None
        presentmon_tailer: PresentMonCsvTailer | None = None
        self._set_runtime(
            state="capturing",
            process_name=match.name,
            pid=match.pid,
            session_id=store.session_id,
            session_dir=str(store.path),
            started_utc_ms=store.started_utc_ms,
            message="正在采集真实游戏数据",
        )
        self._status(store, "process", "ok", details={"pid": match.pid, "name": match.name})
        if self.config.game_log_path.is_file():
            self._status(store, "game_log", "ok", details={"path": str(self.config.game_log_path)})
        else:
            self._status(store, "game_log", "unavailable", f"未找到日志：{self.config.game_log_path}")
        if gpu_sampler and gpu_sampler.available:
            self._status(store, "process_gpu", "ok", details={"scope": "process", "provider": "Windows PDH"})
            process_gpu_status = "ok"
        else:
            self._status(
                store,
                "process_gpu",
                "unavailable",
                gpu_sampler.reason if gpu_sampler else "已禁用",
                {"scope": "process", "provider": "Windows PDH", "auto_retry": bool(gpu_sampler)},
            )
            process_gpu_status = "unavailable"
        if self.config.enable_presentmon:
            presentmon = PresentMonAdapter(self.config.presentmon_path, store.presentmon_csv_path, match.pid, store.session_id)
            if presentmon.start():
                presentmon_tailer = PresentMonCsvTailer(store.presentmon_csv_path, store.session_id, store.started_utc_ms)
                self._status(store, "presentmon", "ok", details={"version": "2.5.1", "pid": match.pid})
            else:
                self._status(store, "presentmon", "unavailable", presentmon.start_error)
        else:
            self._status(store, "presentmon", "unavailable", "已在配置中禁用")
        last_vendor_sample = 0.0
        last_prune = time.monotonic()
        missing_since: float | None = None
        next_tick = time.monotonic()
        try:
            while not self._stop.is_set():
                now_mono = time.monotonic()
                if now_mono < next_tick:
                    self._stop.wait(next_tick - now_mono)
                    continue
                next_tick += self.config.sample_interval_sec
                ts_ms = utc_now_ms()
                elapsed = store.elapsed_ms(ts_ms)
                process_records = sampler.sample(store.session_id, ts_ms, elapsed)
                store.write_metrics(process_records)
                self._remember(process_records, elapsed)
                if any(record.status == "ok" for record in process_records):
                    missing_since = None
                elif missing_since is None:
                    missing_since = now_mono
                if not sampler.is_running():
                    missing_since = missing_since or now_mono
                if missing_since is not None and now_mono - missing_since >= self.config.process_exit_grace_sec:
                    break
                events = log_tailer.poll(store.session_id, ts_ms, store.started_utc_ms)
                if events:
                    store.write_events(events)
                    self._status(store, "game_log", "ok", details={"path": str(self.config.game_log_path), "last_event_utc_ms": events[-1].ts_utc_ms})
                if presentmon_tailer:
                    pm_records = presentmon_tailer.feed_lines(presentmon.drain_lines() if presentmon else [], ts_ms)
                    if pm_records:
                        store.write_metrics(pm_records)
                        self._remember(pm_records, elapsed)
                    if presentmon and presentmon.dropped_lines:
                        self._status(
                            store,
                            "presentmon",
                            "degraded",
                            f"标准输出队列溢出，已丢弃 {presentmon.dropped_lines} 行规范化样本；原始 CSV 仍保留",
                        )
                    if presentmon and not presentmon.running() and presentmon.exit_code() not in (None, 0):
                        self._status(store, "presentmon", "unavailable", f"PresentMon 已退出，代码 {presentmon.exit_code()}")
                        presentmon_tailer = None
                if gpu_sampler:
                    value, reason = gpu_sampler.sample()
                    current_gpu_status = "ok" if value is not None else "unavailable"
                    record = MetricRecord(
                        session_id=store.session_id,
                        ts_utc_ms=ts_ms,
                        elapsed_ms=elapsed,
                        source="windows_pdh",
                        metric="process_gpu_pct",
                        value=value,
                        unit="percent",
                        status="ok" if value is not None else "unavailable",
                        reason=reason,
                        scope="process",
                    )
                    store.write_metric(record)
                    self._remember([record], elapsed)
                    if current_gpu_status != process_gpu_status:
                        self._status(
                            store,
                            "process_gpu",
                            current_gpu_status,
                            reason,
                            {"scope": "process", "provider": "Windows PDH", "auto_retry": True},
                        )
                        process_gpu_status = current_gpu_status
                if vendor_sampler and now_mono - last_vendor_sample >= self.config.vendor_gpu_interval_sec:
                    last_vendor_sample = now_mono
                    values, reason = vendor_sampler.sample()
                    vendor_records: list[MetricRecord] = []
                    for metric, unit in NvidiaDeviceSampler.METRICS:
                        vendor_records.append(
                            MetricRecord(
                                session_id=store.session_id,
                                ts_utc_ms=ts_ms,
                                elapsed_ms=elapsed,
                                source="nvidia-smi",
                                metric=metric,
                                value=values.get(metric),
                                unit=unit,
                                status="ok" if metric in values else "unavailable",
                                reason=None if metric in values else reason,
                                scope="device",
                            )
                        )
                    store.write_metrics(vendor_records)
                    self._remember(vendor_records, elapsed)
                    self._status(
                        store,
                        "vendor_gpu_supplement",
                        "ok" if values else "unavailable",
                        reason,
                        {"scope": "device", "provider": "nvidia-smi"},
                    )
                if now_mono - last_prune >= self.config.prune_interval_sec:
                    last_prune = now_mono
                    store.prune_before(ts_ms - self.config.history_minutes * 60 * 1000)
                if presentmon and store.presentmon_csv_path.is_file():
                    raw_mb = store.presentmon_csv_path.stat().st_size / (1024 * 1024)
                    if raw_mb > self.config.max_presentmon_csv_mb:
                        presentmon.stop()
                        presentmon_tailer = None
                        self._status(
                            store,
                            "presentmon",
                            "unavailable",
                            f"原始 CSV 达到 {self.config.max_presentmon_csv_mb} MiB 容量上限，已停止逐帧采集",
                        )
        finally:
            ts_ms = utc_now_ms()
            # Ask PresentMon to flush/close its CSV before consuming the final rows.
            if presentmon:
                presentmon.stop()
            if presentmon_tailer:
                final_records = presentmon_tailer.feed_lines(presentmon.drain_lines() if presentmon else [], ts_ms, flush=True)
                store.write_metrics(final_records)
                self._remember(final_records, store.elapsed_ms(ts_ms))
            if gpu_sampler:
                gpu_sampler.close()
            store.close(abnormal=False, reason="目标进程退出" if not self._stop.is_set() else "用户停止工具")
            self._store = None
            self._set_runtime(state="stopped" if self._stop.is_set() else "waiting", message="会话已保存")

    def _run_synthetic(self) -> None:
        now = utc_now_ms()
        started = now - 5 * 60 * 1000
        store = SessionStore(
            self.config.sessions_dir,
            process_name="SyntheticGame.exe",
            pid=None,
            environment={**self._host_environment(), "demo_only": True},
            synthetic=True,
            started_utc_ms=started,
        )
        self._store = self._latest_store = store
        for source in ("process", "presentmon", "process_gpu", "game_log"):
            self._status(store, source, "degraded", "显式合成演示数据")
        # Five-minute backfill makes range export immediately demonstrable.
        for second in range(0, 301):
            ts_ms = started + second * 1000
            records = synthetic_metrics(store.session_id, started, ts_ms)
            store.write_metrics(records)
            if second % 37 == 0:
                store.write_event(synthetic_event(store.session_id, started, ts_ms, second // 37))
        self._set_runtime(
            state="capturing",
            process_name="SyntheticGame.exe",
            pid=None,
            session_id=store.session_id,
            session_dir=str(store.path),
            started_utc_ms=started,
            message="合成演示模式：所有数据均非真实采集",
            synthetic=True,
        )
        next_tick = time.monotonic()
        index = 0
        try:
            while not self._stop.is_set():
                now_mono = time.monotonic()
                if now_mono < next_tick:
                    self._stop.wait(next_tick - now_mono)
                    continue
                next_tick += self.config.sample_interval_sec
                ts_ms = utc_now_ms()
                records = synthetic_metrics(store.session_id, started, ts_ms)
                store.write_metrics(records)
                self._remember(records, store.elapsed_ms(ts_ms))
                if index % 37 == 0:
                    store.write_event(synthetic_event(store.session_id, started, ts_ms, index // 37))
                index += 1
        finally:
            store.close(abnormal=False, reason="合成演示结束")
            self._store = None
            self._set_runtime(state="stopped", message="合成演示会话已保存")

    def _status(
        self,
        store: SessionStore,
        name: str,
        status: str,
        reason: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        source = SourceStatus(name=name, status=status, reason=reason, details=details or {})  # type: ignore[arg-type]
        current = store.source_statuses.get(name)
        if current and current.status == source.status and current.reason == source.reason and current.details == source.details:
            return
        store.set_source_status(source)
        with self._lock:
            self._snapshot.sources[name] = source
        self._notify()

    def _remember(self, records: list[MetricRecord], elapsed_ms: int) -> None:
        with self._lock:
            self._snapshot.elapsed_ms = elapsed_ms
            for record in records:
                self._snapshot.last_metrics[record.metric] = record.value
        self._notify()

    def _set_runtime(self, **changes: object) -> None:
        with self._lock:
            for name, value in changes.items():
                setattr(self._snapshot, name, value)
        self._notify()

    def _notify(self) -> None:
        if not self._listeners:
            return
        snapshot = self.snapshot()
        for listener in self._listeners:
            try:
                listener(snapshot)
            except Exception:
                continue

    @staticmethod
    def _host_environment() -> dict[str, object]:
        return {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpus": psutil.cpu_count(logical=True),
            "physical_cpus": psutil.cpu_count(logical=False),
            "memory_bytes": psutil.virtual_memory().total,
        }
