from __future__ import annotations

from game_diag.models import RuntimeSnapshot
from game_diag.ui import DiagnosticApp, _adjust_offset, _ago_text, _duration_text, _gameplay_mouse_active, _set_window_click_through


class TextSink:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class FakeUser32:
    def __init__(self) -> None:
        self.style = 0x00000100
        self.last_hwnd = None
        self.GetAncestor = FakeFunction(lambda _hwnd, _flag: 99)
        self.GetWindowLongW = FakeFunction(lambda _hwnd, _index: self.style)
        self.SetWindowLongW = FakeFunction(self._set_style)

    def _set_style(self, hwnd, _index, style):
        previous = self.style
        self.last_hwnd = getattr(hwnd, "value", hwnd)
        self.style = int(style)
        return previous


class FakeGameplayUser32:
    def __init__(self, foreground_pid: int, cursor_showing: bool) -> None:
        self.foreground_pid = foreground_pid
        self.cursor_showing = cursor_showing
        self.GetForegroundWindow = FakeFunction(lambda: 88)
        self.GetWindowThreadProcessId = FakeFunction(self._get_window_pid)
        self.GetCursorInfo = FakeFunction(self._get_cursor_info)

    def _get_window_pid(self, _hwnd, pid_pointer):
        pid_pointer._obj.value = self.foreground_pid
        return 1

    def _get_cursor_info(self, cursor_pointer):
        cursor_pointer._obj.flags = 1 if self.cursor_showing else 0
        return 1


def test_human_readable_time_labels():
    assert _duration_text(30) == "30秒"
    assert _duration_text(60) == "1分00秒"
    assert _duration_text(80) == "1分20秒"
    assert _ago_text(0) == "现在"
    assert _ago_text(125) == "2分05秒前"


def test_mouse_wheel_offset_is_bounded():
    assert _adjust_offset(60, 120, 60, 300) == 120
    assert _adjust_offset(60, -120, 5, 300) == 55
    assert _adjust_offset(295, 120, 60, 300) == 300
    assert _adjust_offset(3, -120, 5, 300) == 0


def test_overlay_click_through_style_can_be_enabled_and_disabled():
    user32 = FakeUser32()
    mask = 0x00000020 | 0x08000000
    assert _set_window_click_through(42, True, user32)
    assert user32.style & mask == mask
    assert user32.last_hwnd == 99
    assert _set_window_click_through(42, False, user32)
    assert user32.style & mask == 0


def test_passthrough_only_applies_to_foreground_game_with_hidden_cursor():
    assert _gameplay_mouse_active(42, FakeGameplayUser32(42, cursor_showing=False))
    assert not _gameplay_mouse_active(42, FakeGameplayUser32(42, cursor_showing=True))
    assert not _gameplay_mouse_active(42, FakeGameplayUser32(7, cursor_showing=False))


def test_overlay_metric_line_renders_all_available_values():
    app = DiagnosticApp.__new__(DiagnosticApp)
    app.state_text = TextSink()
    app.process_text = TextSink()
    app.metric_text = TextSink()
    app.graphics_text = TextSink()
    app.source_text = TextSink()
    snapshot = RuntimeSnapshot(
        state="capturing",
        process_name="game.exe",
        pid=42,
        last_metrics={"process_cpu_pct": 2.5, "process_memory_mb": 512, "app_fps": 60},
    )
    app._render(snapshot)
    assert app.metric_text.value == "CPU 2.5%   内存 512 MiB"
    assert app.graphics_text.value == "Present FPS 60.0   GPU进程 —"


def test_overlay_prefers_process_gpu_and_labels_device_fallback():
    app = DiagnosticApp.__new__(DiagnosticApp)
    app.state_text = TextSink()
    app.process_text = TextSink()
    app.metric_text = TextSink()
    app.graphics_text = TextSink()
    app.source_text = TextSink()
    app._render(RuntimeSnapshot(state="capturing", last_metrics={"process_gpu_pct": 93.25}))
    assert app.graphics_text.value == "Present FPS —   GPU进程 93.2%"
    app._render(RuntimeSnapshot(state="capturing", last_metrics={"device_gpu_util_pct": 81.0}))
    assert app.graphics_text.value == "Present FPS —   GPU设备 81.0%"
