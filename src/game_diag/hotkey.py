from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Callable


class GlobalHotkey:
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    HOTKEY_ID = 0xB10

    def __init__(self, modifiers: int, virtual_key: int, callback: Callable[[], None]) -> None:
        self.modifiers = modifiers
        self.virtual_key = virtual_key
        self.callback = callback
        self.reason: str | None = None
        self.registered = False
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()

    def start(self) -> bool:
        if os.name != "nt":
            self.reason = "全局快捷键仅支持 Windows"
            return False
        self._thread = threading.Thread(target=self._run, name="global-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)
        return self.registered

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        if not user32.RegisterHotKey(None, self.HOTKEY_ID, self.modifiers, self.virtual_key):
            self.reason = f"RegisterHotKey 失败，错误码 {ctypes.get_last_error()}；可能与其它程序冲突"
            self._ready.set()
            return
        self.registered = True
        self._ready.set()
        message = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == self.WM_HOTKEY and message.wParam == self.HOTKEY_ID:
                    try:
                        self.callback()
                    except Exception:
                        continue
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)
            self.registered = False
