from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SCHEMA_VERSION = "0.1"
Status = Literal["ok", "degraded", "unavailable", "waiting", "stopped"]


@dataclass(slots=True)
class MetricRecord:
    session_id: str
    ts_utc_ms: int
    elapsed_ms: int
    source: str
    metric: str
    value: float | None
    unit: str
    status: Status = "ok"
    reason: str | None = None
    scope: Literal["process", "device", "system"] | None = None
    sample_count: int | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EventRecord:
    session_id: str
    ts_utc_ms: int
    elapsed_ms: int
    source: str
    level: str
    category: str
    message: str
    raw_time: str | None = None
    file: str | None = None
    status: Status = "ok"
    reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceStatus:
    name: str
    status: Status
    reason: str | None = None
    updated_utc_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RuntimeSnapshot:
    state: str = "starting"
    process_name: str | None = None
    pid: int | None = None
    session_id: str | None = None
    session_dir: str | None = None
    started_utc_ms: int | None = None
    elapsed_ms: int = 0
    last_metrics: dict[str, float | None] = field(default_factory=dict)
    sources: dict[str, SourceStatus] = field(default_factory=dict)
    message: str = "正在启动"
    synthetic: bool = False

    def copy(self) -> "RuntimeSnapshot":
        return RuntimeSnapshot(
            state=self.state,
            process_name=self.process_name,
            pid=self.pid,
            session_id=self.session_id,
            session_dir=self.session_dir,
            started_utc_ms=self.started_utc_ms,
            elapsed_ms=self.elapsed_ms,
            last_metrics=dict(self.last_metrics),
            sources={key: SourceStatus(**value.to_dict()) for key, value in self.sources.items()},
            message=self.message,
            synthetic=self.synthetic,
        )
