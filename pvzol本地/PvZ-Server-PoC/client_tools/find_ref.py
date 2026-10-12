"""Find which method bodies reference an AS3 name (class / property / method).

    python client_tools/find_ref.py <swf> "<substr>" [--ctx N]

Prints one line per matching method with the matching disassembly lines, so we
can answer "who creates UpdateUnitPlan?" / "who calls getPlanSetting?".
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abcdis  # noqa: E402


def main(argv):
    path, needle = argv[1], argv[2]
    ctx = 4
    if "--ctx" in argv:
        ctx = int(argv[argv.index("--ctx") + 1])
    needle_l = needle.lower()
    for abc in abcdis.load_swf(path):
        owner = {}
        for c in abc.classes:
            for t in c["traits"] + c.get("inst_traits", []):
                if "method" in t and isinstance(t["name"], str):
                    owner.setdefault(t["method"], []).append(f"{c['name']}.{t['name']}")
                if "method" in t and t.get("kind") in (0x01, 0x02, 0x03):
                    pass
            owner.setdefault(c.get("iinit"), []).append(f"{c['name']}.<init>")
            owner.setdefault(c.get("cinit"), []).append(f"{c['name']}.<cinit>")
        for b in abc.bodies:
            lines = abc.disassemble(b["code"])
            hit = [i for i, ln in enumerate(lines) if needle_l in ln.lower()]
            if not hit:
                continue
            names = ", ".join(sorted(set(owner.get(b["method"], [])))) or f"method {b['method']}"
            print(f"\n### {names}")
            for i in hit:
                lo, hi = max(0, i - ctx), min(len(lines), i + ctx + 1)
                for ln in lines[lo:hi]:
                    print("   ", ln.strip())
                print("    ---")


if __name__ == "__main__":
    main(sys.argv)
