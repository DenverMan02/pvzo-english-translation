"""Native desktop window for the loopback-only PVZ launcher frontend.

The controller owns all data and services. This process only displays its local
HTTP page, so it deliberately exposes no Python API to page JavaScript.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib
import ipaddress
import json
import logging
from pathlib import Path
import sys
from urllib.parse import urlsplit


TITLE = "植物大战僵尸 OL"
LOCALIZATION = {
    "global.quitConfirmation": "确定退出启动器吗？退出后将关闭本次启动的游戏服务。",
    "global.ok": "确定",
    "global.quit": "退出",
    "global.cancel": "取消",
    "global.saveFile": "保存文件",
    "windows.fileFilter.allFiles": "所有文件",
    "windows.fileFilter.otherFiles": "其他文件类型",
}


def validate_local_url(value: str) -> str:
    """Accept literal loopback HTTP addresses only; never resolve remote DNS."""
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise ValueError("启动器地址必须是本机 HTTP 地址。") from exc
    if (
        parsed.scheme.lower() != "http"
        or not address.is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or any(char in value for char in "\r\n\t")
    ):
        raise ValueError("启动器只能打开带端口的本机 HTTP 地址。")
    return value


def check_dependencies() -> dict[str, object]:
    """Import the shipped Windows renderer without creating any window."""
    import webview
    import pythonnet
    import clr
    from webview.util import interop_dll_path

    importlib.import_module("webview.platforms.edgechromium")
    forms = importlib.import_module("webview.platforms.winforms")
    dependencies = {
        name: str(Path(interop_dll_path(name)).resolve())
        for name in (
            "Microsoft.Web.WebView2.Core.dll",
            "Microsoft.Web.WebView2.WinForms.dll",
        )
    }
    for name, location in dependencies.items():
        if not Path(location).is_file():
            raise RuntimeError(f"缺少桌面运行组件：{name}")
    if not forms.is_chromium:
        raise RuntimeError("未找到 Microsoft Edge WebView2 Runtime，请安装后重试。")
    return {
        "ok": True,
        "renderer": "edgechromium",
        "python": sys.version.split()[0],
        "webview": str(Path(webview.__file__).resolve()),
        "pythonnet": str(Path(pythonnet.__file__).resolve()),
        "dependencies": dependencies,
    }


def show_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
    except (AttributeError, OSError):
        if sys.stderr:
            print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PVZ OL 桌面启动器窗口")
    parser.add_argument("--url", help="控制器提供的本机 HTTP 页面地址")
    parser.add_argument("--data-dir", type=Path, help="项目缓存目录的父目录")
    parser.add_argument("--self-test", action="store_true", help="仅导入桌面组件，不打开窗口")
    args = parser.parse_args(argv)

    if args.self_test:
        try:
            report = check_dependencies()
            if sys.stdout:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            if sys.stderr:
                print(f"Desktop dependency check failed: {exc}", file=sys.stderr)
            return 2

    if not args.url or args.data_dir is None:
        parser.error("请提供 --url 和 --data-dir。")
    try:
        url = validate_local_url(args.url)
        data_dir = args.data_dir.resolve()
        cache_dir = data_dir / "cache" / "webview"
        cache_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=data_dir / "ui-shell.log",
            encoding="utf-8",
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(message)s",
        )
        check_dependencies()
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.create_window(
            TITLE,
            url=url,
            width=1280,
            height=860,
            min_size=(960, 700),
            resizable=True,
            frameless=False,
            confirm_close=True,
            localization=LOCALIZATION,
            background_color="#f4f6f2",
            text_select=True,
        )
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=True,
            storage_path=str(cache_dir),
            localization=LOCALIZATION,
        )
        return 0
    except Exception as exc:
        logging.exception("桌面窗口启动失败")
        show_error(f"桌面窗口启动失败：{exc}\n\n请检查项目内的 ui-shell.log。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
