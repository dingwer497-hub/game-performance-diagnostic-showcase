from __future__ import annotations

import os
import queue
import tkinter as tk
from ctypes import Structure, WinDLL, byref, sizeof, wintypes
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from .engine import DiagnosticEngine
from .exporter import RangeExporter
from .hotkey import GlobalHotkey
from .models import RuntimeSnapshot
from .preflight import format_preflight, run_preflight


COLORS = {
    "bg": "#0b0a09",
    "panel": "#191613",
    "line": "#594b3a",
    "ink": "#e2d9cb",
    "muted": "#9e978d",
    "gold": "#c0a16a",
    "green": "#7f9c79",
    "red": "#be5c50",
    "violet": "#9d89b8",
}


def _local_time(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M:%S")


def _duration_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, remainder = divmod(seconds, 60)
    if minutes:
        return f"{minutes}分{remainder:02d}秒"
    return f"{remainder}秒"


def _ago_text(seconds: int) -> str:
    return "现在" if seconds == 0 else f"{_duration_text(seconds)}前"


def _adjust_offset(current: int, wheel_delta: int, step_seconds: int, available_seconds: int) -> int:
    direction = 1 if wheel_delta > 0 else -1
    return max(0, min(available_seconds, current + direction * step_seconds))


def _release_mouse_and_focus(window_handle: int) -> tuple[int | None, int]:
    """Release game mouse ownership and move keyboard/mouse focus to the form."""
    if os.name != "nt":
        return None, 0
    user32 = WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.SetFocus.argtypes = (wintypes.HWND,)
    previous = user32.GetForegroundWindow()
    user32.ReleaseCapture()
    user32.ClipCursor(None)
    shown = 0
    while shown < 16:
        shown += 1
        if user32.ShowCursor(True) >= 0:
            break
    hwnd = wintypes.HWND(window_handle)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    return int(previous) if previous else None, shown


def _restore_foreground(window_handle: int | None, cursor_adjustments: int) -> None:
    if os.name != "nt":
        return
    user32 = WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    for _ in range(cursor_adjustments):
        user32.ShowCursor(False)
    if window_handle and user32.IsWindow(wintypes.HWND(window_handle)):
        user32.SetForegroundWindow(wintypes.HWND(window_handle))


def _set_window_click_through(window_handle: int, enabled: bool = True, user32: object | None = None) -> bool:
    """Toggle mouse pass-through on the overlay's native top-level window."""
    if os.name != "nt" and user32 is None:
        return False
    try:
        api = user32 or WinDLL("user32", use_last_error=True)
        get_style = api.GetWindowLongW
        set_style = api.SetWindowLongW
        get_style.argtypes = (wintypes.HWND, wintypes.INT)
        get_style.restype = wintypes.LONG
        set_style.argtypes = (wintypes.HWND, wintypes.INT, wintypes.LONG)
        set_style.restype = wintypes.LONG
        get_ancestor = getattr(api, "GetAncestor", None)
        hwnd = wintypes.HWND(window_handle)
        if get_ancestor is not None:
            get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
            get_ancestor.restype = wintypes.HWND
            root_hwnd = get_ancestor(hwnd, 2)  # GA_ROOT
            if root_hwnd:
                hwnd = wintypes.HWND(root_hwnd)
        gwl_exstyle = -20
        ws_ex_transparent = 0x00000020
        ws_ex_layered = 0x00080000
        ws_ex_noactivate = 0x08000000
        interaction_mask = ws_ex_transparent | ws_ex_noactivate
        current = int(get_style(hwnd, gwl_exstyle))
        desired = current | interaction_mask | ws_ex_layered if enabled else current & ~interaction_mask
        set_style(hwnd, gwl_exstyle, desired)
        return int(get_style(hwnd, gwl_exstyle)) & interaction_mask == (interaction_mask if enabled else 0)
    except (AttributeError, OSError):
        return False


class _CursorInfo(Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    )


def _gameplay_mouse_active(pid: int | None, user32: object | None = None) -> bool:
    """Return true only while the target game is foreground and hides the cursor."""
    if not pid or (os.name != "nt" and user32 is None):
        return False
    try:
        api = user32 or WinDLL("user32", use_last_error=True)
        api.GetForegroundWindow.restype = wintypes.HWND
        api.GetWindowThreadProcessId.argtypes = (wintypes.HWND, wintypes.LPDWORD)
        api.GetCursorInfo.argtypes = (wintypes.LPVOID,)
        api.GetCursorInfo.restype = wintypes.BOOL
        hwnd = api.GetForegroundWindow()
        if not hwnd:
            return False
        foreground_pid = wintypes.DWORD()
        api.GetWindowThreadProcessId(hwnd, byref(foreground_pid))
        if foreground_pid.value != pid:
            return False
        cursor = _CursorInfo(cbSize=sizeof(_CursorInfo))
        if not api.GetCursorInfo(byref(cursor)):
            return False
        cursor_showing = 0x00000001
        return not bool(cursor.flags & cursor_showing)
    except (AttributeError, OSError):
        return False


class DiagnosticApp:
    def __init__(self, engine: DiagnosticEngine) -> None:
        self.engine = engine
        self.root = tk.Tk()
        self.root.title("性能与日志追溯工具")
        self.root.configure(bg=COLORS["bg"])
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.geometry("410x258+24+90")
        self.root.resizable(False, False)
        self._drag_origin: tuple[int, int] | None = None
        self._commands: queue.Queue[str] = queue.Queue()
        self._dialog: tk.Toplevel | None = None
        self._previous_foreground: int | None = None
        self._cursor_adjustments = 0
        self._build_overlay()
        self.root.update_idletasks()
        self._overlay_passthrough = False
        self.hotkey = GlobalHotkey(
            engine.config.hotkey_modifiers,
            engine.config.hotkey_vk,
            lambda: self._commands.put("open_submission"),
        )
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def run(self) -> None:
        self._show_preflight_if_needed()
        self.engine.start()
        hotkey_ok = self.hotkey.start()
        self.hotkey_text.set("Ctrl+Shift+F10" if hotkey_ok else "快捷键不可用，请点击按钮")
        self.root.after(120, self._poll)
        self.root.mainloop()

    def _show_preflight_if_needed(self) -> None:
        items = run_preflight(self.engine.config)
        marker = self.engine.config.sessions_dir.parent / ".preflight-v1"
        has_problem = any(item.level in {"warning", "error"} for item in items)
        has_error = any(item.level == "error" for item in items)
        if marker.is_file() and not has_error:
            return
        title = "首次启动兼容性检查" if not marker.is_file() else "兼容性检查提示"
        text = format_preflight(items)
        if has_error:
            messagebox.showerror(title, text, parent=self.root)
        elif has_problem:
            messagebox.showwarning(title, text, parent=self.root)
        else:
            messagebox.showinfo(title, text, parent=self.root)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("completed", encoding="ascii")
        except OSError:
            pass

    def close(self) -> None:
        self.hotkey.stop()
        self.engine.stop()
        self.root.destroy()

    def _build_overlay(self) -> None:
        border = tk.Frame(self.root, bg=COLORS["line"], padx=1, pady=1)
        border.pack(fill="both", expand=True)
        panel = tk.Frame(border, bg=COLORS["panel"], padx=16, pady=12)
        panel.pack(fill="both", expand=True)
        header = tk.Frame(panel, bg=COLORS["panel"])
        header.pack(fill="x")
        title = tk.Label(header, text="性能与日志追溯工具", bg=COLORS["panel"], fg=COLORS["ink"], font=("Microsoft YaHei UI", 12, "bold"))
        title.pack(side="left")
        tk.Button(header, text="×", command=self.close, bg=COLORS["panel"], fg=COLORS["muted"], bd=0, font=("Arial", 13), activebackground=COLORS["panel"]).pack(side="right")
        for widget in (header, title):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
        self.state_text = tk.StringVar(value="正在启动")
        self.process_text = tk.StringVar(value="等待目标进程")
        self.metric_text = tk.StringVar(value="CPU —  内存 —")
        self.graphics_text = tk.StringVar(value="Present FPS —  GPU进程 —")
        self.source_text = tk.StringVar(value="性能 —  日志 —")
        self.hotkey_text = tk.StringVar(value="正在注册快捷键")
        tk.Label(panel, textvariable=self.state_text, anchor="w", bg=COLORS["panel"], fg=COLORS["green"], font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", pady=(13, 2))
        tk.Label(panel, textvariable=self.process_text, anchor="w", bg=COLORS["panel"], fg=COLORS["ink"]).pack(fill="x")
        tk.Label(panel, textvariable=self.metric_text, anchor="w", bg=COLORS["panel"], fg=COLORS["gold"]).pack(fill="x", pady=(8, 0))
        tk.Label(panel, textvariable=self.graphics_text, anchor="w", bg=COLORS["panel"], fg=COLORS["gold"]).pack(fill="x")
        tk.Label(panel, textvariable=self.source_text, anchor="w", bg=COLORS["panel"], fg=COLORS["muted"]).pack(fill="x")
        action = tk.Button(
            panel,
            text="提交缺陷反馈",
            command=self.open_submission,
            bg="#3b2d22",
            fg=COLORS["ink"],
            activebackground="#503b2b",
            activeforeground="#ffffff",
            bd=1,
            relief="solid",
            pady=7,
        )
        action.pack(fill="x", pady=(12, 2))
        tk.Label(panel, textvariable=self.hotkey_text, anchor="center", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(fill="x")

    def _poll(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            if command == "open_submission":
                self.open_submission()
        snapshot = self.engine.snapshot()
        self._sync_overlay_interaction(snapshot)
        self._render(snapshot)
        self.root.after(250, self._poll)

    def _sync_overlay_interaction(self, snapshot: RuntimeSnapshot) -> None:
        passthrough = self._dialog is None and _gameplay_mouse_active(snapshot.pid)
        if passthrough == self._overlay_passthrough:
            return
        if _set_window_click_through(self.root.winfo_id(), passthrough):
            self._overlay_passthrough = passthrough

    def _render(self, snapshot: RuntimeSnapshot) -> None:
        state_labels = {
            "waiting": "等待游戏启动",
            "capturing": "采集中" if not snapshot.synthetic else "合成演示采集中",
            "stopped": "会话已保存",
            "error": "采集异常",
        }
        self.state_text.set(state_labels.get(snapshot.state, snapshot.message))
        elapsed = snapshot.elapsed_ms // 1000
        self.process_text.set(f"{snapshot.process_name or '目标未识别'}  PID {snapshot.pid or '—'}  {elapsed // 60:02d}:{elapsed % 60:02d}")
        values = snapshot.last_metrics
        cpu = values.get("process_cpu_pct")
        memory = values.get("process_memory_mb")
        present_fps = values.get("app_fps")
        process_gpu = values.get("process_gpu_pct")
        device_gpu = values.get("device_gpu_util_pct")
        self.metric_text.set(
            (f"CPU {cpu:.1f}%" if cpu is not None else "CPU —")
            + (f"   内存 {memory:.0f} MiB" if memory is not None else "   内存 —")
        )
        gpu_text = (
            f"GPU进程 {process_gpu:.1f}%"
            if process_gpu is not None
            else (f"GPU设备 {device_gpu:.1f}%" if device_gpu is not None else "GPU进程 —")
        )
        fps_text = f"Present FPS {present_fps:.1f}" if present_fps is not None else "Present FPS —"
        self.graphics_text.set(fps_text + f"   {gpu_text}")
        pm = snapshot.sources.get("presentmon")
        log = snapshot.sources.get("game_log")
        self.source_text.set(f"帧数据 {pm.status.upper() if pm else '—'}   游戏日志 {log.status.upper() if log else '—'}")

    def open_submission(self) -> None:
        if self._dialog and self._dialog.winfo_exists():
            self._activate_submission_dialog(self._dialog, remember_previous=False)
            return
        store = self.engine.latest_store
        if store is None:
            messagebox.showinfo("尚无会话", "游戏尚未启动或采集会话尚未建立。", parent=self.root)
            return
        exporter = RangeExporter(
            store.path,
            self.engine.config.chartjs_path,
            self.engine.config.history_minutes,
            self.engine.config.min_selection_seconds,
            self.engine.config.max_selection_seconds,
            self.engine.config.context_seconds,
        )
        bounds = exporter.available_bounds()
        available_seconds = max(0, int((bounds[1] - max(bounds[0], bounds[1] - self.engine.config.history_minutes * 60 * 1000)) / 1000))
        if available_seconds < self.engine.config.min_selection_seconds:
            messagebox.showinfo("采集时间不足", f"至少采集 {self.engine.config.min_selection_seconds} 秒后才能生成反馈。", parent=self.root)
            return
        dialog = self._dialog = tk.Toplevel(self.root)
        dialog.title("缺陷反馈")
        dialog.configure(bg=COLORS["bg"])
        dialog.attributes("-topmost", True)
        dialog.geometry("640x620+420+80")
        dialog.resizable(False, False)
        body = tk.Frame(dialog, bg=COLORS["bg"], padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="缺陷反馈", bg=COLORS["bg"], fg=COLORS["ink"], font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w", pady=(0, 12))

        form = tk.Frame(body, bg=COLORS["bg"])
        form.pack(fill="x")

        def field_label(text: str) -> None:
            label = tk.Frame(form, bg=COLORS["bg"])
            label.pack(fill="x", pady=(0, 4))
            tk.Label(label, text="*", bg=COLORS["bg"], fg=COLORS["red"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
            tk.Label(label, text=text, bg=COLORS["bg"], fg=COLORS["ink"]).pack(side="left", padx=(3, 0))

        def readonly_entry(value: str) -> None:
            entry = tk.Entry(
                form,
                readonlybackground=COLORS["panel"],
                fg=COLORS["ink"],
                bd=1,
                relief="solid",
            )
            entry.insert(0, value)
            entry.configure(state="readonly")
            entry.pack(fill="x", ipady=5, pady=(0, 8))

        field_label("问题描述")
        readonly_entry("场景切换后出现明显卡顿")
        field_label("复现方式")
        readonly_entry("进入复杂场景后快速移动视角")
        field_label("预期结果")
        readonly_entry("场景切换和视角移动过程保持流畅")
        field_label("问题发生时间")
        start_default = min(60, available_seconds)
        if start_default < self.engine.config.min_selection_seconds:
            start_default = self.engine.config.min_selection_seconds
        start_minutes = tk.IntVar(value=start_default // 60)
        start_seconds = tk.IntVar(value=start_default % 60)
        end_minutes = tk.IntVar(value=0)
        end_seconds = tk.IntVar(value=0)
        info = tk.StringVar()
        error = tk.StringVar()

        def offsets() -> tuple[int, int]:
            values = (start_minutes.get(), start_seconds.get(), end_minutes.get(), end_seconds.get())
            if any(value < 0 for value in values) or start_seconds.get() > 59 or end_seconds.get() > 59:
                raise ValueError("秒数请填写 0 至 59")
            return start_minutes.get() * 60 + start_seconds.get(), end_minutes.get() * 60 + end_seconds.get()

        def selection_values() -> tuple[int, int]:
            start_ago, end_ago = offsets()
            return bounds[1] - start_ago * 1000, bounds[1] - end_ago * 1000

        def refresh() -> None:
            try:
                start_ago, end_ago = offsets()
            except (ValueError, tk.TclError) as exc:
                info.set("请完整填写问题的开始和结束时间。")
                error.set(str(exc) or "时间格式不正确")
                return
            selected_start, selected_end = selection_values()
            duration = start_ago - end_ago
            info.set(
                f"问题区间：{_ago_text(start_ago)} → {_ago_text(end_ago)}（持续 {_duration_text(duration)}）\n"
                f"实际时刻：{_local_time(selected_start)} ～ {_local_time(selected_end)}"
            )
            if start_ago > available_seconds or end_ago > available_seconds:
                error.set(f"超出当前可回溯范围：{_duration_text(available_seconds)}")
            elif start_ago <= end_ago:
                error.set("开始时间应早于结束时间；“开始于多久前”应更大")
            elif duration < self.engine.config.min_selection_seconds:
                error.set(f"问题区间不得短于 {_duration_text(self.engine.config.min_selection_seconds)}")
            elif duration > self.engine.config.max_selection_seconds:
                error.set(f"问题区间不得超过 {_duration_text(self.engine.config.max_selection_seconds)}")
            else:
                error.set("")

        preset_frame = tk.Frame(body, bg=COLORS["bg"])
        preset_frame.pack(fill="x", pady=(0, 8))
        tk.Label(preset_frame, text="快速选择", bg=COLORS["bg"], fg=COLORS["gold"]).pack(side="left", padx=(0, 8))

        def set_offsets(start_ago: int, end_ago: int = 0) -> None:
            start_minutes.set(start_ago // 60)
            start_seconds.set(start_ago % 60)
            end_minutes.set(end_ago // 60)
            end_seconds.set(end_ago % 60)
            refresh()

        shortest_preset = min(30, available_seconds)
        presets = (
            (f"最近{_duration_text(shortest_preset)}", shortest_preset, 0),
            ("最近1分钟", 60, 0),
            ("最近2分钟", 120, 0),
            ("1至2分钟前", 120, 60),
            ("最近5分钟", 300, 0),
        )
        for label, preset_start, preset_end in presets:
            if preset_start <= available_seconds and preset_start - preset_end <= self.engine.config.max_selection_seconds:
                tk.Button(
                    preset_frame,
                    text=label,
                    command=lambda start=preset_start, end=preset_end: set_offsets(start, end),
                    bg=COLORS["panel"],
                    fg=COLORS["ink"],
                    activebackground="#3b3026",
                    activeforeground="#ffffff",
                    bd=1,
                    padx=7,
                ).pack(side="left", padx=(0, 5))

        chooser = tk.LabelFrame(body, text=" 时间区间 ", bg=COLORS["panel"], fg=COLORS["gold"], padx=12, pady=9)
        chooser.pack(fill="x")
        tk.Label(
            chooser,
            text="可用鼠标悬停在数字框上滚动以填写时间",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            anchor="w",
        ).grid(row=0, column=0, columnspan=7, sticky="w", pady=(0, 8))

        def add_time_row(row: int, label: str, minute_var: tk.IntVar, second_var: tk.IntVar) -> None:
            tk.Label(chooser, text=label, bg=COLORS["panel"], fg=COLORS["ink"], width=14, anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            minute = tk.Spinbox(chooser, from_=0, to=max(0, available_seconds // 60), textvariable=minute_var, width=5, justify="center", command=refresh)
            minute.grid(row=row, column=1, padx=(4, 3))
            tk.Label(chooser, text="分", bg=COLORS["panel"], fg=COLORS["ink"]).grid(row=row, column=2)
            second = tk.Spinbox(chooser, from_=0, to=59, increment=5, textvariable=second_var, width=5, justify="center", wrap=True, command=refresh)
            second.grid(row=row, column=3, padx=(9, 3))
            tk.Label(chooser, text="秒前", bg=COLORS["panel"], fg=COLORS["ink"]).grid(row=row, column=4, padx=(0, 8))
            for widget in (minute, second):
                widget.bind("<KeyRelease>", lambda _event: refresh())
                widget.bind("<FocusOut>", lambda _event: refresh())

            def adjust_with_wheel(event: tk.Event, step_seconds: int) -> str:
                try:
                    current = minute_var.get() * 60 + second_var.get()
                except tk.TclError:
                    current = 0
                adjusted = _adjust_offset(current, event.delta, step_seconds, available_seconds)
                minute_var.set(adjusted // 60)
                second_var.set(adjusted % 60)
                refresh()
                return "break"

            minute.bind("<MouseWheel>", lambda event: adjust_with_wheel(event, 60))
            second.bind("<MouseWheel>", lambda event: adjust_with_wheel(event, 5))

        add_time_row(1, "问题开始（较早）", start_minutes, start_seconds)
        add_time_row(2, "问题结束（较晚）", end_minutes, end_seconds)
        tk.Label(body, textvariable=info, bg=COLORS["panel"], fg=COLORS["ink"], justify="left", anchor="w", padx=12, pady=10).pack(fill="x", pady=(10, 0))
        tk.Label(body, textvariable=error, bg=COLORS["bg"], fg=COLORS["red"], anchor="w").pack(fill="x", pady=(4, 0))

        def generate() -> None:
            try:
                selected_start, selected_end = selection_values()
                output = self.engine.export_range(selected_start, selected_end)
            except (ValueError, RuntimeError, OSError, tk.TclError) as exc:
                messagebox.showerror("生成失败", str(exc), parent=dialog)
                return
            self._dismiss_submission_dialog(dialog)
            feedback = output / "feedback.html"
            if os.name == "nt":
                os.startfile(feedback)  # type: ignore[attr-defined]
            messagebox.showinfo("证据包已生成", f"已保存到：\n{output}", parent=self.root)

        buttons = tk.Frame(body, bg=COLORS["bg"])
        buttons.pack(fill="x", side="bottom", pady=(14, 0))
        tk.Button(buttons, text="取消", command=lambda: self._dismiss_submission_dialog(dialog), bg=COLORS["panel"], fg=COLORS["muted"], width=12).pack(side="right")
        tk.Button(buttons, text="提交缺陷反馈", command=generate, bg="#4a3627", fg=COLORS["ink"], width=18).pack(side="right", padx=(0, 9))
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._dismiss_submission_dialog(dialog))
        refresh()
        self._activate_submission_dialog(dialog, remember_previous=True)

    def _activate_submission_dialog(self, dialog: tk.Toplevel, remember_previous: bool) -> None:
        dialog.deiconify()
        dialog.update_idletasks()
        previous, cursor_adjustments = _release_mouse_and_focus(dialog.winfo_id())
        if remember_previous:
            self._previous_foreground = previous
            self._cursor_adjustments = cursor_adjustments
        else:
            self._cursor_adjustments += cursor_adjustments
        dialog.lift()
        dialog.focus_force()
        dialog.grab_set()

    def _dismiss_submission_dialog(self, dialog: tk.Toplevel) -> None:
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()
        self._dialog = None
        previous = self._previous_foreground
        cursor_adjustments = self._cursor_adjustments
        self._previous_foreground = None
        self._cursor_adjustments = 0
        self.root.after(10, lambda: _restore_foreground(previous, cursor_adjustments))

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        if self._drag_origin:
            self.root.geometry(f"+{event.x_root - self._drag_origin[0]}+{event.y_root - self._drag_origin[1]}")
