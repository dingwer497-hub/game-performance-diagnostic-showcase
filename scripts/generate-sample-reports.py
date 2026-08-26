from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

from game_diag.config import project_root
from game_diag.exporter import RangeExporter
from game_diag.models import SourceStatus
from game_diag.session import SessionStore
from game_diag.synthetic import synthetic_event, synthetic_metrics


STARTED_UTC_MS = 1_787_622_400_000
SCENARIOS = (
    ("01标准示例", "00000000-0000-4000-8000-000000000601", "standard"),
    ("02badcase-出现异常帧", "00000000-0000-4000-8000-000000000602", "legacy_badcase"),
    ("03解决异常帧后", "00000000-0000-4000-8000-000000000603", "foreground_filter"),
)


def scenario_metrics(session_id: str, started_utc_ms: int, second: int, scenario: str):
    timestamp = started_utc_ms + second * 1000
    records = synthetic_metrics(session_id, started_utc_ms, timestamp)
    values = {record.metric: record for record in records}
    fps = 48.0 + 7.0 * math.sin(second / 13)
    frame_avg = 1000.0 / fps
    values["app_fps"].value = fps
    values["frame_time_avg_ms"].value = frame_avg
    values["frame_time_p95_ms"].value = frame_avg * 1.35
    values["gpu_busy_p95_ms"].value = 16.0 + 3.0 * math.sin(second / 9)

    abnormal_interval = 90 <= second <= 130
    if scenario != "standard" and abnormal_interval:
        values["app_fps"].value = 204.0 + 12.0 * math.sin(second)
        values["frame_time_avg_ms"].value = 1000.0 / values["app_fps"].value
        values["frame_time_p95_ms"].value = 7.5
        values["gpu_busy_p95_ms"].value = 5.0
    if scenario != "standard" and second == 130:
        values["app_fps"].value = 1.2
        values["frame_time_avg_ms"].value = 843.0
        values["frame_time_p95_ms"].value = 8431.87
        values["gpu_busy_p95_ms"].value = 7221.10

    if scenario == "legacy_badcase":
        records = [record for record in records if record.metric != "game_foreground"]
    elif scenario == "foreground_filter":
        values["game_foreground"].value = 0.0 if abnormal_interval else 1.0
    return records


def main() -> None:
    root = project_root()
    output_root = root / "samples" / "reports"
    output_root.mkdir(parents=True, exist_ok=True)
    chartjs = root / ".tools" / "chartjs" / "package" / "dist" / "chart.umd.min.js"

    with tempfile.TemporaryDirectory(prefix="game-diag-samples-") as temporary:
        temporary_root = Path(temporary)
        for index, (folder_name, session_id, scenario) in enumerate(SCENARIOS):
            started = STARTED_UTC_MS + index * 3_600_000
            store = SessionStore(
                temporary_root,
                "SyntheticGame.exe",
                None,
                environment={
                    "platform": "Windows 11 x64（合成示例）",
                    "processor": "Synthetic 10-Core CPU",
                    "logical_cpus": 20,
                    "demo_only": True,
                },
                synthetic=True,
                session_id=session_id,
                started_utc_ms=started,
            )
            for source in ("process", "presentmon", "process_gpu", "game_log"):
                store.set_source_status(SourceStatus(source, "degraded", "显式合成示例数据"))
            for second in range(211):
                timestamp = started + second * 1000
                store.write_metrics(scenario_metrics(store.session_id, store.started_utc_ms, second, scenario))
                if second % 37 == 0:
                    store.write_event(synthetic_event(store.session_id, store.started_utc_ms, timestamp, second // 37))
            store.close(reason="合成示例生成完成")

            exporter = RangeExporter(store.path, chartjs)
            submission = exporter.export(started + 30_000, started + 180_000)
            destination = output_root / folder_name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(submission, destination)
            print(destination / "feedback.html")


if __name__ == "__main__":
    main()
