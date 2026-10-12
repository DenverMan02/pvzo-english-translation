"""Throwaway: find which client SWF modules reference a regex (loads each ABC once)."""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abcadis", os.path.join(HERE, "client_tools", "abcdis.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

pat = re.compile(sys.argv[1])
root = sys.argv[2]

for fn in sorted(os.listdir(root)):
    if not fn.endswith(".swf") or "_" in fn:
        continue
    path = os.path.join(root, fn)
    try:
        abcs = A.load_swf(path)
    except Exception:
        continue
    for abc in abcs:
        for c in abc.classes:
            for q, midx in abc.trait_methods(c, ""):
                body = abc.body_of_method.get(midx)
                if not body:
                    continue
                txt = "\n".join(abc.disassemble(body["code"]))
                if pat.search(txt):
                    print("### %s :: %s.%s (method %d)" % (fn, c["name"], q, midx))
                    for line in txt.splitlines():
                        if pat.search(line):
                            print("      ", line.strip())
