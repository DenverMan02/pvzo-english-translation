#!/bin/bash

# macOS 双击入口：找一个同时带 tkinter 的 Python，再打开图形启动器。
# 某些 Homebrew Python 默认不含 tkinter，所以不能只依赖 PATH 里的 python3。

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PATH_PYTHON="$(command -v python3 2>/dev/null || true)"

for PYTHON_BIN in \
    "${PVZ_PYTHON:-}" \
    "/usr/local/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3" \
    /Library/Frameworks/Python.framework/Versions/*/bin/python3 \
    "$PATH_PYTHON" \
    "/opt/anaconda3/bin/python" \
    "/opt/homebrew/bin/python3" \
    "/usr/bin/python3"
do
    [ -n "$PYTHON_BIN" ] || continue
    [ -x "$PYTHON_BIN" ] || continue
    if "$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1; then
        exec "$PYTHON_BIN" "$SCRIPT_DIR/launcher.py"
    fi
done

MESSAGE="未找到支持 tkinter 的 Python 3。请安装 python.org 版 Python 3 后再试。"
/usr/bin/osascript -e 'display dialog "未找到支持 tkinter 的 Python 3。请安装 python.org 版 Python 3 后再试。" buttons {"好"} default button 1 with icon stop' 2>/dev/null || true
echo "$MESSAGE"
read -r -p "按 Enter 键关闭…" _
exit 1
