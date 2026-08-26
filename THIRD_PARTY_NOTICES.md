# Third-party components

This project currently uses the following open-source components for the Windows demo environment. Downloaded binaries and package caches are local-only under `.tools/` or `.venv/` and are not committed.

| Component | Locked version | Purpose | License | Official source |
| --- | --- | --- | --- | --- |
| CPython | 3.12.13 | Application runtime; Tkinter supplies the initial external overlay UI | PSF License | https://www.python.org/ |
| psutil | 7.2.2 | Process discovery plus CPU and memory telemetry | BSD-3-Clause | https://github.com/giampaolo/psutil |
| PresentMon | 2.5.1 | ETW-based frame presentation and FPS capture without game injection | MIT | https://github.com/GameTechDev/PresentMon |
| Chart.js | 4.5.1 | Offline charts in the generated diagnostic report | MIT | https://github.com/chartjs/Chart.js |
| pytest | 9.1.1 | Development and regression tests only | MIT | https://github.com/pytest-dev/pytest |
| setuptools | 84.0.0 | Local editable installation and build backend | MIT | https://github.com/pypa/setuptools |
| PyInstaller | 6.16.0 | Build-only Windows executable freezer | GPL-2.0-or-later with bootloader exception | https://pyinstaller.org/ |

`nvidia-smi` is already supplied by the installed NVIDIA driver and is treated only as an optional machine-specific device-telemetry supplement, never as a substitute for process-scoped GPU metrics and not as a vendored project dependency.

LibreHardwareMonitor is intentionally not installed for this demo phase, matching the current design scope.
