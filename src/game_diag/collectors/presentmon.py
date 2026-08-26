from __future__ import annotations

import csv
import math
import os
import queue
import statistics
import subprocess
import threading
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from ..models import MetricRecord


def _normalize(row: dict[str, str]) -> dict[str, str]:
    return {key.strip().casefold(): (value or "").strip() for key, value in row.items() if key is not None}


def _float(row: dict[str, str], *names: str) -> float | None:
    for name in names:
        raw = row.get(name.casefold(), "")
        if raw:
            try:
                value = float(raw)
                if math.isfinite(value):
                    return value
            except ValueError:
                pass
    return None


def _parse_iso_ms(value: str) -> int | None:
    if not value:
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone().astimezone(UTC)
    return int(parsed.timestamp() * 1000)


def percentile_nearest(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def aggregate_presentmon_rows(
    rows: Iterable[dict[str, str]],
    session_id: str,
    session_started_utc_ms: int,
    fallback_utc_ms: int | None = None,
) -> list[MetricRecord]:
    buckets: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"frame": [], "gpu": []})
    relative_origin: float | None = None
    fallback = fallback_utc_ms or session_started_utc_ms
    for raw_row in rows:
        row = _normalize(raw_row)
        frame_ms = _float(row, "frametime", "msbetweenpresents", "msbetweendisplaychange")
        gpu_ms = _float(row, "gpubusy", "msgpubusy")
        timestamp_ms = _parse_iso_ms(row.get("cpustartdatetime", "") or row.get("cpustarttime", ""))
        if timestamp_ms is None:
            relative_ms = _float(row, "cpustarttime")
            relative_seconds = _float(row, "timeinseconds")
            if relative_ms is not None:
                timestamp_ms = session_started_utc_ms + int(relative_ms)
            elif relative_seconds is not None:
                if relative_origin is None:
                    relative_origin = relative_seconds
                timestamp_ms = session_started_utc_ms + int((relative_seconds - relative_origin) * 1000)
            else:
                timestamp_ms = fallback
        bucket = (timestamp_ms // 1000) * 1000
        if frame_ms is not None and frame_ms > 0:
            buckets[bucket]["frame"].append(frame_ms)
        if gpu_ms is not None and gpu_ms >= 0:
            buckets[bucket]["gpu"].append(gpu_ms)
    return _records_from_buckets(buckets, session_id, session_started_utc_ms)


def _records_from_buckets(
    buckets: dict[int, dict[str, list[float]]],
    session_id: str,
    session_started_utc_ms: int,
) -> list[MetricRecord]:
    records: list[MetricRecord] = []
    for bucket, values in sorted(buckets.items()):
        frames = values["frame"]
        gpu_values = values["gpu"]
        elapsed = max(0, bucket - session_started_utc_ms)
        frame_total = sum(frames)
        fps = (len(frames) * 1000 / frame_total) if frame_total > 0 else None
        for metric, value, unit, count in (
            ("app_fps", fps, "fps", len(frames)),
            ("frame_time_avg_ms", statistics.fmean(frames) if frames else None, "ms", len(frames)),
            ("frame_time_p95_ms", percentile_nearest(frames, 0.95), "ms", len(frames)),
            ("gpu_busy_median_ms", statistics.median(gpu_values) if gpu_values else None, "ms", len(gpu_values)),
            ("gpu_busy_p95_ms", percentile_nearest(gpu_values, 0.95), "ms", len(gpu_values)),
        ):
            records.append(
                MetricRecord(
                    session_id=session_id,
                    ts_utc_ms=bucket,
                    elapsed_ms=elapsed,
                    source="presentmon",
                    metric=metric,
                    value=value,
                    unit=unit,
                    status="ok" if value is not None else "unavailable",
                    reason=None if value is not None else "该秒没有有效样本",
                    scope="process",
                    sample_count=count,
                )
            )
    return records


class PresentMonAdapter:
    def __init__(self, executable: Path, output_csv: Path, pid: int, session_name: str) -> None:
        self.executable = executable
        self.output_csv = output_csv
        self.pid = pid
        # One fixed ETW name lets --stop_existing_session recover after an app crash.
        self.session_name = "GameDiag-MVP"
        self.process: subprocess.Popen[str] | None = None
        self.start_error: str | None = None
        self._lines: queue.Queue[str] = queue.Queue(maxsize=20_000)
        self._reader: threading.Thread | None = None
        self.dropped_lines = 0

    def start(self) -> bool:
        if not self.executable.is_file():
            self.start_error = f"PresentMon 不存在：{self.executable}"
            return False
        self.output_csv.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(self.executable),
            "--process_id",
            str(self.pid),
            "--output_stdout",
            "--v2_metrics",
            "--no_console_stats",
            "--terminate_on_proc_exit",
            "--session_name",
            self.session_name,
            "--stop_existing_session",
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
            self._reader = threading.Thread(target=self._read_stdout, name="presentmon-output", daemon=True)
            self._reader.start()
            return True
        except (OSError, subprocess.SubprocessError) as error:
            self.start_error = f"{type(error).__name__}: {error}"
            return False

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def exit_code(self) -> int | None:
        return self.process.poll() if self.process else None

    def drain_lines(self, limit: int = 20_000) -> list[str]:
        lines: list[str] = []
        for _ in range(limit):
            try:
                lines.append(self._lines.get_nowait())
            except queue.Empty:
                break
        return lines

    def _read_stdout(self) -> None:
        if not self.process or not self.process.stdout:
            return
        try:
            with self.output_csv.open("w", encoding="utf-8", newline="", buffering=64 * 1024) as raw:
                for line in self.process.stdout:
                    raw.write(line)
                    try:
                        self._lines.put_nowait(line)
                    except queue.Full:
                        self.dropped_lines += 1
        except OSError as error:
            self.start_error = f"原始 PresentMon 输出写入失败：{type(error).__name__}: {error}"

    def stop(self) -> None:
        if not self.process:
            return
        if self.process.poll() is not None:
            if self._reader and self._reader.is_alive():
                self._reader.join(timeout=2)
            return
        # Stopping the ETW session first lets PresentMon flush and exit cleanly. A raw
        # TerminateProcess can leave a GameDiag trace behind until reboot.
        try:
            subprocess.run(
                ["logman", "stop", self.session_name, "-ets"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=2)


class PresentMonCsvTailer:
    def __init__(self, path: Path, session_id: str, session_started_utc_ms: int) -> None:
        self.path = path
        self.session_id = session_id
        self.session_started_utc_ms = session_started_utc_ms
        self.header: list[str] | None = None
        self.offset = 0
        self.buckets: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"frame": [], "gpu": []})
        self.relative_origin: float | None = None
        self.bad_rows = 0

    def poll(self, now_utc_ms: int, flush: bool = False) -> list[MetricRecord]:
        self._ingest(now_utc_ms)
        return self._flush_ready(now_utc_ms, flush)

    def feed_lines(self, lines: Iterable[str], now_utc_ms: int, flush: bool = False) -> list[MetricRecord]:
        for line in lines:
            if not line.strip():
                continue
            try:
                values = next(csv.reader([line]))
            except csv.Error:
                self.bad_rows += 1
                continue
            if self.header is None:
                self.header = values
                continue
            if len(values) != len(self.header):
                self.bad_rows += 1
                continue
            self._add_row(dict(zip(self.header, values)), now_utc_ms)
        return self._flush_ready(now_utc_ms, flush, use_data_watermark=True)

    def _flush_ready(self, now_utc_ms: int, flush: bool, use_data_watermark: bool = False) -> list[MetricRecord]:
        if flush:
            cutoff = now_utc_ms
        elif use_data_watermark and self.buckets:
            # STDOUT can be momentarily empty in the middle of a second. Since rows
            # are ordered by CPUStartTime, seeing the next bucket is the reliable
            # signal that the preceding bucket is complete.
            cutoff = max(self.buckets) - 1
        else:
            cutoff = (now_utc_ms // 1000 - 1) * 1000
        ready = {key: self.buckets.pop(key) for key in sorted(self.buckets) if key <= cutoff}
        return _records_from_buckets(ready, self.session_id, self.session_started_utc_ms)

    def _ingest(self, now_utc_ms: int) -> None:
        if not self.path.exists():
            return
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0
            self.header = None
        with self.path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            handle.seek(self.offset)
            if self.header is None:
                header_line = handle.readline()
                if not header_line.endswith(("\n", "\r")):
                    return
                self.header = next(csv.reader([header_line]))
            while True:
                start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith(("\n", "\r")):
                    handle.seek(start)
                    break
                try:
                    values = next(csv.reader([line]))
                    if len(values) != len(self.header):
                        self.bad_rows += 1
                        continue
                    self._add_row(dict(zip(self.header, values)), now_utc_ms)
                except (csv.Error, ValueError):
                    self.bad_rows += 1
            self.offset = handle.tell()

    def _add_row(self, raw_row: dict[str, str], now_utc_ms: int) -> None:
        row = _normalize(raw_row)
        frame_ms = _float(row, "frametime", "msbetweenpresents", "msbetweendisplaychange")
        gpu_ms = _float(row, "gpubusy", "msgpubusy")
        timestamp_ms = _parse_iso_ms(row.get("cpustartdatetime", "") or row.get("cpustarttime", ""))
        if timestamp_ms is None:
            relative_ms = _float(row, "cpustarttime")
            relative_seconds = _float(row, "timeinseconds")
            if relative_ms is not None:
                timestamp_ms = self.session_started_utc_ms + int(relative_ms)
            elif relative_seconds is not None:
                if self.relative_origin is None:
                    self.relative_origin = relative_seconds
                timestamp_ms = self.session_started_utc_ms + int((relative_seconds - self.relative_origin) * 1000)
            else:
                timestamp_ms = now_utc_ms
        bucket = (timestamp_ms // 1000) * 1000
        if frame_ms is not None and frame_ms > 0:
            self.buckets[bucket]["frame"].append(frame_ms)
        if gpu_ms is not None and gpu_ms >= 0:
            self.buckets[bucket]["gpu"].append(gpu_ms)
