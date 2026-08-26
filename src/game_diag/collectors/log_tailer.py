from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from ..models import EventRecord


_B1_LINE = re.compile(
    r"^\[(?P<time>\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]"
    r"\[\s*(?P<frame>\d+)\](?P<body>.*)$"
)


def parse_log_line(
    line: str,
    collected_utc_ms: int,
    session_started_utc_ms: int,
    time_is_utc: bool = True,
) -> tuple[int, int, str, str, str | None]:
    clean = line.lstrip("\ufeff").rstrip("\r\n")
    match = _B1_LINE.match(clean)
    raw_time: str | None = None
    ts_ms = collected_utc_ms
    body = clean
    if match:
        raw_time = match.group("time")
        parsed = datetime.strptime(raw_time, "%Y.%m.%d-%H.%M.%S:%f")
        if time_is_utc:
            parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.astimezone().astimezone(UTC)
        ts_ms = int(parsed.timestamp() * 1000)
        body = match.group("body").strip()
    category = body.split(":", 1)[0].strip() if ":" in body else "Log"
    lowered = body.casefold()
    if ": error:" in lowered or lowered.startswith("error"):
        level = "ERROR"
    elif ": warning:" in lowered or "warning" in category.casefold():
        level = "WARNING"
    else:
        level = "INFO"
    return ts_ms, max(0, ts_ms - session_started_utc_ms), level, category, raw_time


class LogTailer:
    def __init__(self, path: Path, time_is_utc: bool = True, start_at_end: bool = False) -> None:
        self.path = path
        self.time_is_utc = time_is_utc
        self.offset = 0
        self.file_identity: tuple[int, int] | None = None
        self.partial = b""
        self.start_at_end = start_at_end
        self._initialized = False

    def poll(self, session_id: str, collected_utc_ms: int, session_started_utc_ms: int) -> list[EventRecord]:
        if not self.path.exists():
            return []
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if not self._initialized:
            self.file_identity = identity
            self.offset = stat.st_size if self.start_at_end else 0
            self._initialized = True
        elif self.file_identity != identity or stat.st_size < self.offset:
            self.file_identity = identity
            self.offset = 0
            self.partial = b""
        if stat.st_size == self.offset:
            return []
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except (PermissionError, OSError):
            return []
        data = self.partial + chunk
        parts = data.splitlines(keepends=True)
        if parts and not parts[-1].endswith((b"\n", b"\r")):
            self.partial = parts.pop()
        else:
            self.partial = b""
        events: list[EventRecord] = []
        for raw_line in parts:
            line = raw_line.decode("utf-8-sig", errors="replace").rstrip("\r\n")
            if not line:
                continue
            ts_ms, elapsed_ms, level, category, raw_time = parse_log_line(
                line,
                collected_utc_ms,
                session_started_utc_ms,
                self.time_is_utc,
            )
            events.append(
                EventRecord(
                    session_id=session_id,
                    ts_utc_ms=ts_ms,
                    elapsed_ms=elapsed_ms,
                    source="game_log",
                    level=level,
                    category=category,
                    message=line.lstrip("\ufeff"),
                    raw_time=raw_time,
                    file=str(self.path),
                )
            )
        return events
