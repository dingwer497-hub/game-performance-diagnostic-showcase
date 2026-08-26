from __future__ import annotations

import json
import time

from game_diag.config import AppConfig
from game_diag.engine import DiagnosticEngine
from game_diag.exporter import RangeExporter


def test_synthetic_engine_runs_and_exports(tmp_path):
    config = AppConfig(
        synthetic=True,
        sessions_dir=tmp_path / "sessions",
        chartjs_path=tmp_path / "missing-chart.js",
        sample_interval_sec=0.02,
    )
    engine = DiagnosticEngine(config)
    engine.start()
    deadline = time.monotonic() + 3
    while engine.latest_store is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine.latest_store is not None
    time.sleep(0.08)
    engine.stop()
    store = engine.latest_store
    assert store is not None
    manifest = json.loads((store.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True
    assert manifest["abnormal_end"] is False
    exporter = RangeExporter(store.path, config.chartjs_path)
    start, end = exporter.available_bounds()
    output = exporter.export(end - 60_000, end)
    assert (output / "feedback.html").is_file()


def test_normal_engine_waits_without_fabricating_session(tmp_path):
    config = AppConfig(
        process_name="process-that-does-not-exist-987654.exe",
        process_path_contains="",
        sessions_dir=tmp_path / "sessions",
        process_wait_interval_sec=0.02,
    )
    engine = DiagnosticEngine(config)
    engine.start()
    time.sleep(0.08)
    snapshot = engine.snapshot()
    engine.stop()
    assert snapshot.state == "waiting"
    assert engine.snapshot().state == "stopped"
    assert engine.latest_store is None
    assert list(config.sessions_dir.iterdir()) == []
