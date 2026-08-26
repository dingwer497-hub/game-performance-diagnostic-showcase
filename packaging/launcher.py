from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path


def _ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _prepare_tk_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    tcl_source, tk_source = bundle / "_tcl_data", bundle / "_tk_data"
    if _ascii_path(tcl_source) and _ascii_path(tk_source):
        os.environ["TCL_LIBRARY"], os.environ["TK_LIBRARY"] = str(tcl_source), str(tk_source)
        return
    init_text = (tcl_source / "init.tcl").read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"package require -exact Tcl ([0-9.]+)", init_text)
    version = match.group(1) if match else "8.6"
    system_drive = os.environ.get("SystemDrive", "C:").rstrip("\\/")
    candidates = tuple(
        Path(value)
        for value in (
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("PUBLIC"),
            str(Path(system_drive + "\\") / "Users" / "Public"),
        )
        if value
    )
    last_error: OSError | None = None
    for public_root in candidates:
        target = public_root / "GamePerformanceLogTrace" / f"tk-runtime-{version}"
        if not _ascii_path(target):
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            tcl_target, tk_target = target / "tcl", target / "tk"
            if not (tcl_target / "init.tcl").is_file():
                shutil.copytree(tcl_source, tcl_target, dirs_exist_ok=True)
            if not (tk_target / "tk.tcl").is_file():
                shutil.copytree(tk_source, tk_target, dirs_exist_ok=True)
            if _ascii_path(tcl_target) and _ascii_path(tk_target):
                os.environ["TCL_LIBRARY"], os.environ["TK_LIBRARY"] = str(tcl_target), str(tk_target)
                return
        except OSError as error:
            last_error = error
    raise RuntimeError(
        "Tcl/Tk cannot start from a non-ASCII folder and no writable ASCII cache is available. "
        "Please extract the application to an English-only path."
    ) from last_error


_prepare_tk_runtime()

from game_diag.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
