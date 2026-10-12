"""Find every method body that mentions a given symbol.

    python client_tools/_probe.py <swf> --grep "<regex>" [<classSubstr>]
"""
from __future__ import annotations

import os
import re
import sys
import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abcadis", os.path.join(_HERE, "abcdis.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


def main(argv):
    path = argv[1]
    pat = re.compile(argv[argv.index("--grep") + 1])
    rest = argv[argv.index("--grep") + 2:]
    cls_filter = rest[0] if rest else ""
    for abc in A.load_swf(path):
        for c in abc.classes:
            if cls_filter and cls_filter.lower() not in c["name"].lower():
                continue
            for q, midx in abc.trait_methods(c, ""):
                body = abc.body_of_method.get(midx)
                if not body:
                    continue
                txt = "\n".join(abc.disassemble(body["code"]))
                if pat.search(txt):
                    print(f"--- {c['name']}.{q} (method {midx})")
                    for line in txt.splitlines():
                        if pat.search(line):
                            print("      ", line.strip())


if __name__ == "__main__":
    main(sys.argv)
