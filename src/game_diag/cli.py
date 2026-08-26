from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .config import AppConfig
from .engine import DiagnosticEngine
from .exporter import RangeExporter
from .preflight import run_preflight
from .ui import DiagnosticApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="游戏缺陷现场性能与日志追溯 Demo")
    parser.add_argument("--demo-data", action="store_true", help="使用明确标注的合成数据演示完整闭环")
    parser.add_argument("--headless", action="store_true", help="无界面运行，用于自动验证")
    parser.add_argument("--duration", type=float, default=10.0, help="无界面模式运行秒数")
    parser.add_argument("--export-last", type=int, default=0, metavar="SECONDS", help="结束前导出最近 N 秒")
    parser.add_argument("--process-name", default="b1-Win64-Shipping.exe")
    parser.add_argument("--log-path", type=Path)
    parser.add_argument("--sessions-dir", type=Path)
    parser.add_argument("--no-presentmon", action="store_true")
    parser.add_argument("--no-process-gpu", action="store_true")
    parser.add_argument("--no-vendor-gpu", action="store_true")
    parser.add_argument("--preflight", action="store_true", help="输出本机兼容性检查后退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AppConfig(
        process_name=args.process_name,
        synthetic=args.demo_data,
        enable_presentmon=not args.no_presentmon,
        enable_process_gpu=not args.no_process_gpu,
        enable_vendor_gpu_metrics=not args.no_vendor_gpu,
    )
    if args.log_path:
        config.game_log_path = args.log_path
    if args.sessions_dir:
        config.sessions_dir = args.sessions_dir
    if args.preflight:
        items = run_preflight(config)
        print(json.dumps({"preflight": [item.to_dict() for item in items]}, ensure_ascii=False, indent=2))
        return 2 if any(item.level == "error" for item in items) else 0
    engine = DiagnosticEngine(config)
    if not args.headless:
        DiagnosticApp(engine).run()
        return 0
    engine.start()
    try:
        deadline = time.monotonic() + max(0.1, args.duration)
        while time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        engine.stop()
    result: dict[str, object] = {"snapshot": asdict(engine.snapshot())}
    store = engine.latest_store
    if store:
        result["session_dir"] = str(store.path)
        if args.export_last:
            exporter = RangeExporter(
                store.path,
                config.chartjs_path,
                config.history_minutes,
                config.min_selection_seconds,
                config.max_selection_seconds,
                config.context_seconds,
            )
            start, end = exporter.available_bounds()
            duration_ms = max(config.min_selection_seconds * 1000, args.export_last * 1000)
            selected_start = max(start, end - duration_ms)
            if end - selected_start >= config.min_selection_seconds * 1000:
                output = exporter.export(selected_start, end)
                result["submission_dir"] = str(output)
                result["feedback"] = str(output / "feedback.html")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
