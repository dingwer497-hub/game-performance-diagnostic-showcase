from __future__ import annotations

import math
import random

from .models import EventRecord, MetricRecord


def synthetic_metrics(session_id: str, started_utc_ms: int, ts_utc_ms: int) -> list[MetricRecord]:
    elapsed_ms = max(0, ts_utc_ms - started_utc_ms)
    seconds = elapsed_ms / 1000
    hitch = 1.0 if int(seconds) % 73 in (0, 1, 2) else 0.0
    fps = max(18.0, 58.0 + 4.0 * math.sin(seconds / 11) - 31.0 * hitch)
    frame_avg = 1000.0 / fps
    values = (
        ("process_cpu_pct", 35.0 + 8.0 * math.sin(seconds / 17) + 16.0 * hitch, "percent", "process"),
        ("process_memory_mb", 6120.0 + seconds * 0.7 + 80.0 * math.sin(seconds / 29), "MiB", "process"),
        ("app_fps", fps, "fps", "process"),
        ("frame_time_avg_ms", frame_avg, "ms", "process"),
        ("frame_time_p95_ms", frame_avg * (1.25 + 2.1 * hitch), "ms", "process"),
        ("gpu_busy_p95_ms", 11.0 + 3.0 * math.sin(seconds / 13) + 13.0 * hitch, "ms", "process"),
        ("process_gpu_pct", min(100.0, 72.0 + 9.0 * math.sin(seconds / 9) + 17.0 * hitch), "percent", "process"),
        ("device_gpu_temperature_c", 63.0 + 2.0 * math.sin(seconds / 31), "celsius", "device"),
        ("game_foreground", 1.0, "boolean", "process"),
    )
    records: list[MetricRecord] = []
    for metric, value, unit, scope in values:
        records.append(
            MetricRecord(
                session_id=session_id,
                ts_utc_ms=ts_utc_ms,
                elapsed_ms=elapsed_ms,
                source="synthetic_demo",
                metric=metric,
                value=float(value),
                unit=unit,
                status="degraded",
                reason="显式合成演示数据，不是真实游戏采集",
                scope=scope,  # type: ignore[arg-type]
                sample_count=60 if metric.startswith(("app_", "frame_", "gpu_busy")) else 1,
            )
        )
    return records


def synthetic_event(session_id: str, started_utc_ms: int, ts_utc_ms: int, index: int) -> EventRecord:
    categories = (
        ("LogTexture", "WARNING", "演示：纹理流送等待与帧时间尖峰同时出现"),
        ("LogStreaming", "INFO", "演示：资源流送批次完成"),
        ("LogRenderer", "ERROR", "演示：渲染任务超过预期预算"),
    )
    category, level, message = categories[index % len(categories)]
    return EventRecord(
        session_id=session_id,
        ts_utc_ms=ts_utc_ms,
        elapsed_ms=max(0, ts_utc_ms - started_utc_ms),
        source="synthetic_demo",
        level=level,
        category=category,
        message=message,
        raw_time=None,
        file=None,
        status="degraded",
        reason="显式合成演示事件，不是真实游戏日志",
    )
