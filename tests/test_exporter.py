from __future__ import annotations

import csv
import json

import pytest

from game_diag.exporter import RangeExporter
from game_diag.models import EventRecord, MetricRecord, SourceStatus
from game_diag.session import SessionStore


def make_session(tmp_path, synthetic=False, with_foreground=True):
    started = 1_700_000_000_000
    store = SessionStore(tmp_path, "game.exe", 42, synthetic=synthetic, started_utc_ms=started)
    store.set_source_status(SourceStatus("process", "ok"))
    store.set_source_status(SourceStatus("game_log", "ok"))
    for second in range(0, 121):
        ts = started + second * 1000
        store.write_metric(MetricRecord(store.session_id, ts, second * 1000, "psutil", "process_cpu_pct", float(second % 80), "percent", scope="process"))
        store.write_metric(MetricRecord(store.session_id, ts, second * 1000, "presentmon", "app_fps", 60.0, "fps", scope="process"))
        if with_foreground:
            store.write_metric(MetricRecord(store.session_id, ts, second * 1000, "win32_foreground", "game_foreground", 1.0, "boolean", scope="process"))
    store.write_event(EventRecord(store.session_id, started + 60_000, 60_000, "game_log", "ERROR", "LogGame", "token=secret crash", file=r"C:\Users\tester\AppData\Local\b1\Saved\Logs\b1.log"))
    store.close()
    return store, started


def test_export_creates_offline_evidence(tmp_path):
    store, started = make_session(tmp_path)
    chart = tmp_path / "chart.umd.min.js"
    chart.write_text("window.Chart=function(){};", encoding="utf-8")
    exporter = RangeExporter(store.path, chart)
    output = exporter.export(started + 40_000, started + 100_000)
    expected = {"selection.json", "range-summary.json", "metrics.csv", "events.jsonl", "game-log.txt", "source-status.json", "feedback.html"}
    assert expected.issubset({item.name for item in output.iterdir()})
    assert (output / "assets" / "chart.umd.min.js").is_file()
    selection = json.loads((output / "selection.json").read_text(encoding="utf-8"))
    assert selection["export_start_utc_ms"] == started + 25_000
    assert selection["export_end_utc_ms"] == started + 115_000
    report = (output / "feedback.html").read_text(encoding="utf-8")
    assert "QA 完整性视图" in report
    assert "研发诊断视图" in report
    assert "缺陷反馈" in report
    assert "问题描述" in report
    assert "复现方式" in report
    assert "预期结果" in report
    assert "用户上报问题发生时间段" in report
    assert "开始：" in report
    assert "结束：" in report
    assert "真实选择" not in report
    assert "演示数据" not in report
    assert "assets/chart.umd.min.js" in report
    dev_view = report[report.index('<section class="view" id="dev">'):]
    assert dev_view.index("原始证据") < dev_view.index("区间摘要")
    assert "这五个文件分别是什么？" in dev_view
    assert "相同事件已合并" in dev_view
    assert 'data-log-level="ERROR"' in dev_view
    assert "首次出现" in dev_view
    assert "最后出现" in dev_view


def test_export_redacts_sensitive_log_value(tmp_path):
    store, started = make_session(tmp_path)
    output = RangeExporter(store.path, tmp_path / "missing.js").export(started + 40_000, started + 100_000)
    assert "secret" not in (output / "game-log.txt").read_text(encoding="utf-8")
    assert "[REDACTED]" in (output / "game-log.txt").read_text(encoding="utf-8")
    report = (output / "feedback.html").read_text(encoding="utf-8")
    assert "secret" not in report
    event = json.loads((output / "events.jsonl").read_text(encoding="utf-8"))
    assert event["file"] == "b1.log"
    assert not (output / "assets").exists()


@pytest.mark.parametrize("start_offset,end_offset,message", [
    (0, 5, "短于"),
    (0, 700, "超过"),
    (-1, 20, "最近 30 分钟"),
])
def test_export_rejects_invalid_ranges(tmp_path, start_offset, end_offset, message):
    store, started = make_session(tmp_path)
    exporter = RangeExporter(store.path, tmp_path / "missing.js")
    with pytest.raises(ValueError, match=message):
        exporter.export(started + start_offset * 1000, started + end_offset * 1000)


def test_metrics_csv_contains_context_and_sources(tmp_path):
    store, started = make_session(tmp_path)
    output = RangeExporter(store.path, tmp_path / "missing.js").export(started + 40_000, started + 100_000)
    with (output / "metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert min(int(row["ts_utc_ms"]) for row in rows) == started + 25_000
    assert {row["source"] for row in rows} == {"psutil", "presentmon", "win32_foreground"}


def test_synthetic_report_has_unmistakable_banner(tmp_path):
    store, started = make_session(tmp_path, synthetic=True)
    output = RangeExporter(store.path, tmp_path / "missing.js").export(started + 40_000, started + 100_000)
    assert "合成测试报告" in (output / "feedback.html").read_text(encoding="utf-8")


def test_confirmed_background_is_excluded_from_summary_but_kept_raw(tmp_path):
    store, started = make_session(tmp_path)
    background_ts = started + 60_000
    with (store.path / "metrics.jsonl").open("a", encoding="utf-8") as handle:
        for record in (
            MetricRecord(store.session_id, background_ts, 60_000, "win32_foreground", "game_foreground", 0.0, "boolean", scope="process"),
            MetricRecord(store.session_id, background_ts, 60_000, "presentmon", "app_fps", 999.0, "fps", scope="process"),
            MetricRecord(store.session_id, background_ts, 60_000, "presentmon", "frame_time_p95_ms", 9000.0, "ms", scope="process"),
        ):
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    output = RangeExporter(store.path, tmp_path / "missing.js").export(started + 40_000, started + 100_000)
    selection = json.loads((output / "selection.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "range-summary.json").read_text(encoding="utf-8"))
    assert selection["foreground_state_available"] is True
    assert selection["background_seconds_excluded"] == 1
    assert summary["metrics"]["app_fps"]["maximum"] == 60.0
    assert "999.0" in (output / "metrics.csv").read_text(encoding="utf-8-sig")
    assert "已从摘要和主曲线排除" in (output / "feedback.html").read_text(encoding="utf-8")


def test_legacy_session_warns_about_extreme_gap_without_deleting_it(tmp_path):
    store, started = make_session(tmp_path, with_foreground=False)
    with (store.path / "metrics.jsonl").open("a", encoding="utf-8") as handle:
        record = MetricRecord(store.session_id, started + 60_000, 60_000, "presentmon", "frame_time_p95_ms", 8431.0, "ms", scope="process")
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    output = RangeExporter(store.path, tmp_path / "missing.js").export(started + 40_000, started + 100_000)
    selection = json.loads((output / "selection.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "range-summary.json").read_text(encoding="utf-8"))
    report = (output / "feedback.html").read_text(encoding="utf-8")
    assert selection["foreground_state_available"] is False
    assert selection["suspicious_frame_gap_count"] == 1
    assert summary["metrics"]["frame_time_p95_ms"]["maximum"] == 8431.0
    assert "可能来自切后台或暂停" in report
