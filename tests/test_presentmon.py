from __future__ import annotations

import csv
from datetime import UTC, datetime

import pytest

from game_diag.collectors.presentmon import PresentMonCsvTailer, aggregate_presentmon_rows, percentile_nearest


def test_nearest_rank_percentile():
    assert percentile_nearest([1, 2, 3, 4, 100], 0.95) == 100
    assert percentile_nearest([], 0.95) is None


def test_presentmon_aggregates_by_second():
    fixture = "tests/fixtures/presentmon_sample.csv"
    with open(fixture, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    started = int(datetime(2026, 8, 24, 9, 18, 0, tzinfo=UTC).timestamp() * 1000)
    records = aggregate_presentmon_rows(rows, "session", started)
    first = {record.metric: record for record in records if record.ts_utc_ms % 60_000 == 38_000}
    assert first["app_fps"].value == pytest.approx(66.666666, rel=1e-5)
    assert first["frame_time_avg_ms"].value == 15.0
    assert first["frame_time_p95_ms"].value == 20.0
    assert first["gpu_busy_median_ms"].value == 10.0
    assert first["gpu_busy_p95_ms"].value == 12.0
    assert first["app_fps"].sample_count == 2


def test_missing_gpu_values_are_unavailable():
    rows = [{"CPUStartTime": "2026-08-24T09:18:38+00:00", "MsBetweenPresents": "16.0"}]
    started = int(datetime(2026, 8, 24, 9, 18, tzinfo=UTC).timestamp() * 1000)
    records = aggregate_presentmon_rows(rows, "session", started)
    gpu = next(record for record in records if record.metric == "gpu_busy_p95_ms")
    assert gpu.value is None
    assert gpu.status == "unavailable"
    assert gpu.reason


def test_presentmon_251_v2_relative_columns_map_to_session_utc():
    started = int(datetime(2026, 8, 25, 6, 30, tzinfo=UTC).timestamp() * 1000)
    rows = [
        {"CPUStartTime": "26.8522", "FrameTime": "19.4572", "GPUBusy": "20.4464"},
        {"CPUStartTime": "46.3094", "FrameTime": "19.7765", "GPUBusy": "20.7587"},
    ]
    records = aggregate_presentmon_rows(rows, "session", started)
    assert {record.ts_utc_ms for record in records} == {started}
    values = {record.metric: record.value for record in records}
    assert values["frame_time_avg_ms"] == pytest.approx((19.4572 + 19.7765) / 2)
    assert values["gpu_busy_p95_ms"] == pytest.approx(20.7587)


def test_csv_tailer_keeps_gpu_values_in_their_own_second(tmp_path):
    csv_path = tmp_path / "presentmon.csv"
    csv_path.write_text(
        "CPUStartTime,MsBetweenPresents,MsGPUBusy\n"
        "2026-08-24T09:18:38+00:00,10,8\n"
        "2026-08-24T09:18:38+00:00,20,12\n"
        "2026-08-24T09:18:39+00:00,25,4\n",
        encoding="utf-8",
    )
    started = int(datetime(2026, 8, 24, 9, 18, 0, tzinfo=UTC).timestamp() * 1000)
    tailer = PresentMonCsvTailer(csv_path, "session", started)
    records = tailer.poll(started + 40_000, flush=True)
    by_second = {
        second: {record.metric: record.value for record in records if record.ts_utc_ms == second}
        for second in {record.ts_utc_ms for record in records}
    }
    second_38 = int(datetime(2026, 8, 24, 9, 18, 38, tzinfo=UTC).timestamp() * 1000)
    second_39 = second_38 + 1_000
    assert by_second[second_38]["gpu_busy_p95_ms"] == 12
    assert by_second[second_39]["gpu_busy_p95_ms"] == 4


def test_stream_tailer_parses_live_presentmon_251_output(tmp_path):
    started = int(datetime(2026, 8, 25, 6, 30, tzinfo=UTC).timestamp() * 1000)
    tailer = PresentMonCsvTailer(tmp_path / "unused.csv", "session", started)
    records = tailer.feed_lines(
        [
            "Application,ProcessID,CPUStartTime,FrameTime,GPUBusy\n",
            "b1-Win64-Shipping.exe,42,26.8522,19.4572,20.4464\n",
            "b1-Win64-Shipping.exe,42,46.3094,19.7765,20.7587\n",
        ],
        started + 2_000,
        flush=True,
    )
    values = {record.metric: record.value for record in records}
    assert values["app_fps"] == pytest.approx(2_000 / (19.4572 + 19.7765))
    assert values["gpu_busy_p95_ms"] == pytest.approx(20.7587)


def test_stream_tailer_does_not_split_a_second_during_temporary_queue_gap(tmp_path):
    started = int(datetime(2026, 8, 25, 6, 30, tzinfo=UTC).timestamp() * 1000)
    tailer = PresentMonCsvTailer(tmp_path / "unused.csv", "session", started)
    assert tailer.feed_lines(
        [
            "Application,ProcessID,CPUStartTime,FrameTime,GPUBusy\n",
            "game.exe,42,26.0,10.0,8.0\n",
        ],
        started + 5_000,
    ) == []
    assert tailer.feed_lines(["game.exe,42,46.0,20.0,12.0\n"], started + 5_100) == []
    records = tailer.feed_lines(["game.exe,42,1026.0,16.0,9.0\n"], started + 5_200)
    first = {record.metric: record for record in records}
    assert first["app_fps"].sample_count == 2
    assert first["app_fps"].value == pytest.approx(2_000 / 30)
