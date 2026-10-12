"""Find methods whose disassembly mentions a needle string.  Usage: _findstr2.py <swf> <needle>"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("abcadis", "PvZ-Server-PoC/client_tools/abcdis.py")
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
path, needle = sys.argv[1], sys.argv[2]
for abc in A.load_swf(path):
    for c in abc.classes:
        for q, midx in abc.trait_methods(c, ""):
            body = abc.body_of_method.get(midx)
            if not body:
                continue
            lines = abc.disassemble(body["code"])
            if any(needle in l for l in lines):
                print(f"### {c['name']}.{q} (method {midx})")
                for i, l in enumerate(lines):
                    print(f"   {i:4d} {l.strip()}")
