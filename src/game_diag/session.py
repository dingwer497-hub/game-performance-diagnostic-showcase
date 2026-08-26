from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .models import EventRecord, MetricRecord, SourceStatus


def utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


def iso_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat(timespec="milliseconds")


class SessionStore:
    """Append-only session store that leaves readable evidence after a crash."""

    def __init__(
        self,
        sessions_dir: Path,
        process_name: str,
        pid: int | None,
        environment: dict[str, Any] | None = None,
        synthetic: bool = False,
        session_id: str | None = None,
        started_utc_ms: int | None = None,
    ) -> None:
        self.session_id = session_id or str(uuid.uuid4())
        self.started_utc_ms = started_utc_ms or utc_now_ms()
        stamp = datetime.fromtimestamp(self.started_utc_ms / 1000, tz=UTC).strftime("%Y%m%d_%H%M%S")
        self.path = sessions_dir / f"{stamp}_{self.session_id[:8]}"
        self.raw_dir = self.path / "raw"
        self.submissions_dir = self.path / "submissions"
        self.raw_dir.mkdir(parents=True, exist_ok=False)
        self.submissions_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_path = self.path / "metrics.jsonl"
        self.events_path = self.path / "events.jsonl"
        self.presentmon_csv_path = self.raw_dir / "presentmon.csv"
        self._lock = threading.RLock()
        self._metrics_file = self.metrics_path.open("a", encoding="utf-8", buffering=1, newline="\n")
        self._events_file = self.events_path.open("a", encoding="utf-8", buffering=1, newline="\n")
        self._closed = False
        self.source_statuses: dict[str, SourceStatus] = {}
        self.manifest: dict[str, Any] = {
            "schema_version": "0.1",
            "session_id": self.session_id,
            "started_utc_ms": self.started_utc_ms,
            "started_utc": iso_utc(self.started_utc_ms),
            "ended_utc_ms": None,
            "ended_utc": None,
            "process": {"name": process_name, "pid": pid},
            "environment": environment or {},
            "synthetic": synthetic,
            "abnormal_end": True,
            "source_statuses": {},
        }
        self._write_manifest()

    def elapsed_ms(self, ts_utc_ms: int | None = None) -> int:
        return max(0, (ts_utc_ms or utc_now_ms()) - self.started_utc_ms)

    def write_metric(self, record: MetricRecord) -> None:
        with self._lock:
            self._ensure_open()
            self._metrics_file.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")

    def write_metrics(self, records: Iterable[MetricRecord]) -> None:
        for record in records:
            self.write_metric(record)

    def write_event(self, record: EventRecord) -> None:
        with self._lock:
            self._ensure_open()
            self._events_file.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")

    def write_events(self, records: Iterable[EventRecord]) -> None:
        for record in records:
            self.write_event(record)

    def set_source_status(self, status: SourceStatus) -> None:
        with self._lock:
            status.updated_utc_ms = status.updated_utc_ms or utc_now_ms()
            self.source_statuses[status.name] = status
            self.manifest["source_statuses"] = {
                key: value.to_dict() for key, value in sorted(self.source_statuses.items())
            }
            self._write_manifest()

    def update_process_pid(self, pid: int) -> None:
        with self._lock:
            self.manifest["process"]["pid"] = pid
            self._write_manifest()

    def prune_before(self, cutoff_utc_ms: int) -> dict[str, int]:
        """Compact normalized JSONL files to the configured rolling window."""
        with self._lock:
            self._ensure_open()
            self._metrics_file.flush()
            self._events_file.flush()
            self._metrics_file.close()
            self._events_file.close()
            try:
                kept_metrics = self._compact_jsonl(self.metrics_path, cutoff_utc_ms)
                kept_events = self._compact_jsonl(self.events_path, cutoff_utc_ms)
            finally:
                # A failed compaction must not leave the active capture session unwritable.
                self._metrics_file = self.metrics_path.open("a", encoding="utf-8", buffering=1, newline="\n")
                self._events_file = self.events_path.open("a", encoding="utf-8", buffering=1, newline="\n")
            self.manifest["rolling_window"] = {
                "cutoff_utc_ms": cutoff_utc_ms,
                "kept_metric_records": kept_metrics,
                "kept_event_records": kept_events,
            }
            self._write_manifest()
            return {"metrics": kept_metrics, "events": kept_events}

    def close(self, abnormal: bool = False, reason: str | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            ended = utc_now_ms()
            self.manifest["ended_utc_ms"] = ended
            self.manifest["ended_utc"] = iso_utc(ended)
            self.manifest["abnormal_end"] = abnormal
            if reason:
                self.manifest["end_reason"] = reason
            self.manifest["source_statuses"] = {
                key: value.to_dict() for key, value in sorted(self.source_statuses.items())
            }
            self._metrics_file.flush()
            self._events_file.flush()
            os.fsync(self._metrics_file.fileno())
            os.fsync(self._events_file.fileno())
            self._metrics_file.close()
            self._events_file.close()
            self._closed = True
            self._write_manifest()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("session store is closed")

    @staticmethod
    def _compact_jsonl(path: Path, cutoff_utc_ms: int) -> int:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        kept = 0
        with path.open("r", encoding="utf-8", errors="replace") as source, temp_path.open("w", encoding="utf-8", newline="\n") as target:
            for line in source:
                try:
                    item = json.loads(line)
                    timestamp = int(item.get("ts_utc_ms", -1))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if timestamp >= cutoff_utc_ms:
                    target.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                    kept += 1
        os.replace(temp_path, path)
        return kept

    def _write_manifest(self) -> None:
        manifest_path = self.path / "manifest.json"
        temp_path = self.path / "manifest.json.tmp"
        temp_path.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, manifest_path)

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close(abnormal=exc is not None, reason=str(exc) if exc else None)
