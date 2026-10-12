"""Scan local SWFs for a string / class name in their ABC string pools.

    python client_tools/find_str.py "<substr>" [swf_root]

Prints "path :: <string>" for the first N matches so we can locate which module
owns a class (e.g. UpdateUnitPlan, UnitFactory) without needing the full app.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abcdis  # noqa: E402


def main(argv):
    needle = argv[1]
    root = argv[2] if len(argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "webroot")
    needle_l = needle.lower()
    hits = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if not fn.lower().endswith(".swf"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                for blob in abcdis.abc_blobs(path):
                    abc = abcdis.Abc(blob).load()
                    for s in abc.strings:
                        if s and needle_l in s.lower():
                            print(f"{os.path.relpath(path, root)} :: {s}")
                            hits += 1
                            break
            except Exception:
                continue
    print(f"--- {hits} file(s) matched ---")


if __name__ == "__main__":
    main(sys.argv)
