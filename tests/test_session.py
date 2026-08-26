from __future__ import annotations

import json

from game_diag.models import EventRecord, MetricRecord, SourceStatus
from game_diag.session import SessionStore


def test_session_is_recoverable_before_clean_close(tmp_path):
    store = SessionStore(tmp_path, "game.exe", 123, started_utc_ms=1_000_000)
    manifest = json.loads((store.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["abnormal_end"] is True
    assert manifest["process"]["pid"] == 123
    store.close()


def test_session_writes_jsonl_and_clean_manifest(tmp_path):
    store = SessionStore(tmp_path, "game.exe", 123, started_utc_ms=1_000_000)
    store.write_metric(MetricRecord(store.session_id, 1_001_000, 1_000, "test", "cpu", 10.0, "percent"))
    store.write_event(EventRecord(store.session_id, 1_001_500, 1_500, "log", "ERROR", "Test", "boom"))
    store.set_source_status(SourceStatus("test", "ok"))
    store.close(abnormal=False)
    metric = json.loads((store.path / "metrics.jsonl").read_text(encoding="utf-8"))
    event = json.loads((store.path / "events.jsonl").read_text(encoding="utf-8"))
    manifest = json.loads((store.path / "manifest.json").read_text(encoding="utf-8"))
    assert metric["value"] == 10.0
    assert event["level"] == "ERROR"
    assert manifest["abnormal_end"] is False
    assert manifest["source_statuses"]["test"]["status"] == "ok"


def test_close_is_idempotent(tmp_path):
    store = SessionStore(tmp_path, "game.exe", None)
    store.close()
    store.close()


def test_prune_keeps_only_rolling_window_records(tmp_path):
    store = SessionStore(tmp_path, "game.exe", 1, started_utc_ms=1_000_000)
    store.write_metric(MetricRecord(store.session_id, 1_001_000, 1_000, "test", "cpu", 1.0, "percent"))
    store.write_metric(MetricRecord(store.session_id, 1_100_000, 100_000, "test", "cpu", 2.0, "percent"))
    result = store.prune_before(1_050_000)
    store.close()
    lines = (store.path / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert result == {"metrics": 1, "events": 0}
    assert len(lines) == 1
    assert json.loads(lines[0])["value"] == 2.0
