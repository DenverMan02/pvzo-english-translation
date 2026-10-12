"""Ad-hoc probe: list / disassemble classes in a SWF.

    python client_tools/_probe.py <swf> --list "<substr>"
    python client_tools/_probe.py <swf> --dis "<ClassSubstr>" ["<methodSubstr>"]
"""
from __future__ import annotations

import os
import sys
import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abcadis", os.path.join(_HERE, "abcdis.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


def main(argv):
    path = argv[1]
    abcs = A.load_swf(path)
    if "--list" in argv:
        needle = argv[argv.index("--list") + 1].lower()
        for abc in abcs:
            for c in abc.classes:
                if needle in c["name"].lower():
                    print(c["name"])
                    for q, m in abc.trait_methods(c, ""):
                        print("     ", q, m)
        return
    if "--dis" in argv:
        cls = argv[argv.index("--dis") + 1]
        rest = argv[argv.index("--dis") + 2:]
        method = rest[0] if rest else ""
        for abc in abcs:
            for c in abc.classes:
                if cls.lower() not in c["name"].lower():
                    continue
                for q, midx in abc.trait_methods(c, method):
                    body = abc.body_of_method.get(midx)
                    print(f"\n=== {c['name']}.{q}  (method {midx})")
                    if not body:
                        print("    <no body>")
                        continue
                    print("\n".join(abc.disassemble(body["code"])))


if __name__ == "__main__":
    main(sys.argv)
