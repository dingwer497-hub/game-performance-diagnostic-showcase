from __future__ import annotations

import csv
import json
import math
import re
import shutil
import statistics
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .report import build_feedback_html
from .session import utc_now_ms


_SENSITIVE = re.compile(
    r"(?i)(token|cookie|authorization|password|passwd|session[_-]?id)\s*[:=]\s*([^\s,;]+)"
)


def _redact(text: str) -> str:
    return _SENSITIVE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class RangeExporter:
    def __init__(
        self,
        session_dir: Path,
        chartjs_path: Path,
        history_minutes: int = 30,
        min_selection_seconds: int = 10,
        max_selection_seconds: int = 600,
        context_seconds: int = 15,
    ) -> None:
        self.session_dir = session_dir
        self.chartjs_path = chartjs_path
        self.history_ms = history_minutes * 60 * 1000
        self.min_selection_ms = min_selection_seconds * 1000
        self.max_selection_ms = max_selection_seconds * 1000
        self.context_ms = context_seconds * 1000
        self.manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))

    def export(self, selected_start_utc_ms: int, selected_end_utc_ms: int) -> Path:
        bounds = self.available_bounds()
        self._validate(selected_start_utc_ms, selected_end_utc_ms, bounds)
        export_start = max(bounds[0], selected_start_utc_ms - self.context_ms)
        export_end = min(bounds[1], selected_end_utc_ms + self.context_ms)
        submission_id = f"SUB-{uuid.uuid4().hex[:12].upper()}"
        destination = self.session_dir / "submissions" / submission_id
        destination.mkdir(parents=True, exist_ok=False)
        selection = {
            "schema_version": "0.1",
            "submission_id": submission_id,
            "session_id": self.manifest["session_id"],
            "created_utc_ms": utc_now_ms(),
            "selected_start_utc_ms": selected_start_utc_ms,
            "selected_end_utc_ms": selected_end_utc_ms,
            "selected_duration_ms": selected_end_utc_ms - selected_start_utc_ms,
            "export_start_utc_ms": export_start,
            "export_end_utc_ms": export_end,
            "context_seconds": self.context_ms // 1000,
            "clipped_to_session": export_start > selected_start_utc_ms - self.context_ms or export_end < selected_end_utc_ms + self.context_ms,
            "demo_only_fields": True,
            "synthetic_evidence": bool(self.manifest.get("synthetic")),
        }
        metrics = [
            item
            for item in _read_jsonl(self.session_dir / "metrics.jsonl")
            if export_start <= int(item.get("ts_utc_ms", -1)) <= export_end
        ]
        events = [
            item
            for item in _read_jsonl(self.session_dir / "events.jsonl")
            if export_start <= int(item.get("ts_utc_ms", -1)) <= export_end
        ]
        safe_events = [self._sanitize_event(event) for event in events]
        view_metrics, foreground_info = self._filter_background_metrics(
            metrics,
            selected_start_utc_ms,
            selected_end_utc_ms,
        )
        selection.update(foreground_info)
        summary = self._summary(view_metrics, selected_start_utc_ms, selected_end_utc_ms)
        statuses = self._source_status(metrics, safe_events)
        _write_json(destination / "selection.json", selection)
        _write_json(destination / "range-summary.json", summary)
        _write_json(destination / "source-status.json", statuses)
        self._write_metrics_csv(destination / "metrics.csv", metrics)
        self._write_events(destination / "events.jsonl", safe_events)
        self._write_log(destination / "game-log.txt", safe_events)
        chart_available = self.chartjs_path.is_file()
        if chart_available:
            assets = destination / "assets"
            assets.mkdir()
            shutil.copy2(self.chartjs_path, assets / "chart.umd.min.js")
        feedback = build_feedback_html(
            self.manifest,
            selection,
            summary,
            view_metrics,
            safe_events,
            statuses,
            chart_available,
        )
        (destination / "feedback.html").write_text(feedback, encoding="utf-8")
        return destination

    @staticmethod
    def _filter_background_metrics(
        metrics: list[dict[str, Any]],
        selected_start: int,
        selected_end: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        foreground_by_second: dict[int, bool] = {}
        for record in metrics:
            if record.get("metric") != "game_foreground":
                continue
            value = record.get("value")
            if isinstance(value, (int, float)) and math.isfinite(value):
                foreground_by_second[int(record.get("ts_utc_ms", -1)) // 1000] = float(value) >= 0.5

        background_seconds = {second for second, foreground in foreground_by_second.items() if not foreground}
        view_metrics = [
            record
            for record in metrics
            if record.get("metric") == "game_foreground"
            or int(record.get("ts_utc_ms", -1)) // 1000 not in background_seconds
        ]
        selected_background = sorted(
            second
            for second in background_seconds
            if selected_start // 1000 <= second <= selected_end // 1000
        )
        ranges: list[dict[str, int]] = []
        for second in selected_background:
            if not ranges or second * 1000 > ranges[-1]["end_utc_ms"]:
                ranges.append({"start_utc_ms": second * 1000, "end_utc_ms": (second + 1) * 1000})
            else:
                ranges[-1]["end_utc_ms"] = (second + 1) * 1000

        suspicious_gaps = sum(
            1
            for record in metrics
            if record.get("metric") == "frame_time_p95_ms"
            and selected_start <= int(record.get("ts_utc_ms", -1)) <= selected_end
            and isinstance(record.get("value"), (int, float))
            and float(record["value"]) >= 1000
        )
        return view_metrics, {
            "foreground_state_available": bool(foreground_by_second),
            "background_seconds_excluded": len(selected_background),
            "background_ranges_utc_ms": ranges,
            "suspicious_frame_gap_count": suspicious_gaps,
            "view_excludes_confirmed_background": bool(foreground_by_second),
        }

    def available_bounds(self) -> tuple[int, int]:
        start = int(self.manifest["started_utc_ms"])
        end = int(self.manifest.get("ended_utc_ms") or utc_now_ms())
        timestamps = [
            int(item["ts_utc_ms"])
            for path in (self.session_dir / "metrics.jsonl", self.session_dir / "events.jsonl")
            for item in _read_jsonl(path)
            if item.get("ts_utc_ms") is not None
        ]
        if timestamps:
            start = max(start, min(timestamps))
            end = min(end, max(timestamps)) if self.manifest.get("ended_utc_ms") else max(timestamps)
        if end < start:
            end = start
        return start, end

    def _validate(self, start: int, end: int, bounds: tuple[int, int]) -> None:
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间")
        duration = end - start
        if duration < self.min_selection_ms:
            raise ValueError(f"问题区间不得短于 {self.min_selection_ms // 1000} 秒")
        if duration > self.max_selection_ms:
            raise ValueError(f"问题区间不得超过 {self.max_selection_ms // 1000} 秒")
        allowed_start = max(bounds[0], bounds[1] - self.history_ms)
        if start < allowed_start or end > bounds[1]:
            raise ValueError("选择超出当前会话最近 30 分钟的可用范围")

    @staticmethod
    def _summary(metrics: list[dict[str, Any]], selected_start: int, selected_end: int) -> dict[str, Any]:
        groups: dict[str, list[float]] = defaultdict(list)
        units: dict[str, str] = {}
        for record in metrics:
            ts_ms = int(record.get("ts_utc_ms", -1))
            value = record.get("value")
            if not (selected_start <= ts_ms <= selected_end) or not isinstance(value, (int, float)) or not math.isfinite(value):
                continue
            metric = str(record.get("metric"))
            groups[metric].append(float(value))
            units[metric] = str(record.get("unit") or "")
        output: dict[str, Any] = {"selected_start_utc_ms": selected_start, "selected_end_utc_ms": selected_end, "metrics": {}}
        for metric, values in sorted(groups.items()):
            ordered = sorted(values)
            p95_index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
            output["metrics"][metric] = {
                "count": len(values),
                "minimum": min(values),
                "maximum": max(values),
                "average": statistics.fmean(values),
                "p95": ordered[p95_index],
                "unit": units[metric],
            }
        return output

    def _source_status(self, metrics: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
        source_map = dict(self.manifest.get("source_statuses") or {})
        coverage: dict[str, list[int]] = defaultdict(list)
        for item in [*metrics, *events]:
            if item.get("source") and item.get("ts_utc_ms") is not None:
                coverage[str(item["source"])].append(int(item["ts_utc_ms"]))
        aliases = {"psutil": "process", "windows_pdh": "process_gpu", "nvidia-smi": "vendor_gpu_supplement", "game_log": "game_log", "presentmon": "presentmon", "synthetic_demo": "synthetic_demo"}
        for source, timestamps in coverage.items():
            name = aliases.get(source, source)
            entry = source_map.setdefault(name, {"name": name, "status": "ok", "reason": None, "details": {}})
            entry["coverage"] = {"start_utc_ms": min(timestamps), "end_utc_ms": max(timestamps), "sample_count": len(timestamps)}
        return {"session_id": self.manifest["session_id"], "synthetic": bool(self.manifest.get("synthetic")), "sources": source_map}

    @staticmethod
    def _write_metrics_csv(path: Path, metrics: list[dict[str, Any]]) -> None:
        fields = ["schema_version", "session_id", "ts_utc_ms", "elapsed_ms", "source", "metric", "value", "unit", "status", "reason", "scope", "sample_count"]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(metrics)

    @staticmethod
    def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
        safe = dict(event)
        safe["message"] = _redact(str(safe.get("message") or ""))
        if safe.get("file"):
            safe["file"] = Path(str(safe["file"])).name
        return safe

    @staticmethod
    def _write_log(path: Path, events: list[dict[str, Any]]) -> None:
        lines = []
        for event in events:
            if event.get("source") not in ("game_log", "synthetic_demo"):
                continue
            lines.append(_redact(str(event.get("message") or "")))
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
