"""Find methods that push a given string constant, in one SWF.
Usage: _findstr.py <swf> "<needle>"
"""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath("PvZ-Server-PoC/client_tools/abcdis.py"))
spec = importlib.util.spec_from_file_location("abcadis", "PvZ-Server-PoC/client_tools/abcdis.py")
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

path, needle = sys.argv[1], sys.argv[2]
for abc in A.load_swf(path):
    idx = [i for i, s in enumerate(abc.strings) if needle in s]
    print("string indices:", idx, [abc.strings[i] for i in idx])
    want = set(idx)
    for c in abc.classes:
        for q, midx in abc.trait_methods(c, ""):
            body = abc.body_of_method.get(midx)
            if not body:
                continue
            lines = abc.disassemble(body["code"])
            hits = [l for l in lines if "pushstring" in l]
            # find pushstring with the index
            for l in hits:
                for wi in want:
                    if f" {wi}" in l or f"[{wi}]" in l:
                        print(f"--- {c['name']}.{q} (method {midx})")
                        for x in lines:
                            print("      ", x.strip())
                        break
