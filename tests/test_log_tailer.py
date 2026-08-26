from __future__ import annotations

from datetime import UTC, datetime

from game_diag.collectors.log_tailer import LogTailer, parse_log_line


def test_parse_black_myth_log_time_as_utc():
    line = "[2026.08.24-09.18.39:583][661]LogTexture: Error: loading failed"
    started = int(datetime(2026, 8, 24, 9, 18, 0, tzinfo=UTC).timestamp() * 1000)
    ts_ms, elapsed, level, category, raw_time = parse_log_line(line, started, started, True)
    assert datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat(timespec="milliseconds") == "2026-08-24T09:18:39.583+00:00"
    assert elapsed == 39_583
    assert level == "ERROR"
    assert category == "LogTexture"
    assert raw_time == "2026.08.24-09.18.39:583"


def test_unstructured_line_uses_collection_time():
    ts_ms, elapsed, level, category, raw_time = parse_log_line("plain line", 12_000, 10_000, True)
    assert (ts_ms, elapsed, level, category, raw_time) == (12_000, 2_000, "INFO", "Log", None)


def test_tailer_reads_append_and_handles_truncation(tmp_path):
    path = tmp_path / "b1.log"
    path.write_text("\ufeff[2026.08.24-09.00.00:000][  0]LogInit: started\n", encoding="utf-8")
    tailer = LogTailer(path)
    first = tailer.poll("session", 1, 0)
    assert len(first) == 1
    assert first[0].category == "LogInit"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("[2026.08.24-09.00.01:000][  1]LogGame: Warning: slow\n")
    second = tailer.poll("session", 2, 0)
    assert len(second) == 1
    assert second[0].level == "WARNING"
    path.write_text("[2026.08.24-09.00.02:000][  2]LogGame: Error: reset\n", encoding="utf-8")
    third = tailer.poll("session", 3, 0)
    assert len(third) == 1
    assert third[0].level == "ERROR"


def test_tailer_keeps_partial_line_until_newline(tmp_path):
    path = tmp_path / "b1.log"
    path.write_bytes(b"partial")
    tailer = LogTailer(path)
    assert tailer.poll("session", 2_000, 1_000) == []
    with path.open("ab") as handle:
        handle.write(b" completed\n")
    events = tailer.poll("session", 3_000, 1_000)
    assert len(events) == 1
    assert events[0].message == "partial completed"
