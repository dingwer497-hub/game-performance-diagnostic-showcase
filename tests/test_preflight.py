from __future__ import annotations

from pathlib import Path

from game_diag import config as config_module
from game_diag.config import AppConfig
from game_diag import preflight as preflight_module


def test_frozen_paths_use_bundle_resources_and_local_app_data(monkeypatch, tmp_path: Path):
    bundle = tmp_path / "bundle"
    local = tmp_path / "local"
    monkeypatch.setattr(config_module, "frozen_app", lambda: True)
    monkeypatch.setattr(config_module.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    config = AppConfig()

    assert config.presentmon_path == bundle / "presentmon" / "PresentMon-2.5.1-x64.exe"
    assert config.chartjs_path == bundle / "chartjs" / "chart.umd.min.js"
    assert config.sessions_dir == local / "GamePerformanceLogTrace" / "sessions"


def test_preflight_reports_required_and_optional_components(monkeypatch, tmp_path: Path):
    presentmon = tmp_path / "PresentMon.exe"
    presentmon.write_bytes(b"test")
    config = AppConfig(
        presentmon_path=presentmon,
        chartjs_path=tmp_path / "missing-chart.js",
        game_log_path=tmp_path / "missing-b1.log",
        sessions_dir=tmp_path / "sessions",
    )
    monkeypatch.setattr(preflight_module, "_has_presentmon_permission", lambda: True)
    monkeypatch.setattr(preflight_module.platform, "machine", lambda: "AMD64")

    items = {item.key: item for item in preflight_module.run_preflight(config)}

    assert items["windows_x64"].level == "ok"
    assert items["presentmon"].level == "ok"
    assert items["chartjs"].level == "warning"
    assert items["etw_permission"].level == "ok"
    assert items["storage"].level == "ok"
    assert items["game_log"].level == "warning"
