#!/bin/bash

# macOS launcher: find a Python build with tkinter, then open the graphical launcher.
# Some Homebrew Python builds omit tkinter, so do not rely only on python3 in PATH.

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

MESSAGE="Python 3 with tkinter was not found. Install Python 3 from python.org and try again."
/usr/bin/osascript -e 'display dialog "Python 3 with tkinter was not found. Install Python 3 from python.org and try again." buttons {"OK"} default button 1 with icon stop' 2>/dev/null || true
echo "$MESSAGE"
read -r -p "Press Enter to close…" _
exit 1
