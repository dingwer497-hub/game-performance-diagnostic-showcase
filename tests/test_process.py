from __future__ import annotations

import game_diag.collectors.process as process_module


class FakeCall:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class FakeUser32:
    def __init__(self, foreground_pid: int):
        self.GetForegroundWindow = FakeCall(lambda: 100)

        def write_pid(_window, pointer):
            pointer._obj.value = foreground_pid
            return 1

        self.GetWindowThreadProcessId = FakeCall(write_pid)


def test_process_foreground_matches_window_owner(monkeypatch):
    monkeypatch.setattr(process_module, "WinDLL", lambda *_args, **_kwargs: FakeUser32(42))
    assert process_module.process_is_foreground(42) is True
    assert process_module.process_is_foreground(7) is False
