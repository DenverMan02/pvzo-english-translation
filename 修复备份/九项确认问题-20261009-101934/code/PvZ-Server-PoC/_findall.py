"""_findall.py <needle> [swfglob] — find methods mentioning needle across SWFs."""
import importlib.util, sys, glob, os, re
spec = importlib.util.spec_from_file_location("abcadis", "PvZ-Server-PoC/client_tools/abcdis.py")
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
needle = sys.argv[1]
pats = sys.argv[2] if len(sys.argv) > 2 else "webroot/Module*.swf"
rx = re.compile(re.escape(needle))
for path in sorted(glob.glob(pats)):
    base = os.path.basename(path)
    if re.search(r"_[0-9a-f]{32}\.swf$", base):
        continue
    try:
        for abc in A.load_swf(path):
            for c in abc.classes:
                for q, midx in abc.trait_methods(c, ""):
                    body = abc.body_of_method.get(midx)
                    if not body:
                        continue
                    txt = "\n".join(A.Abc.disassemble(abc, body["code"])) if hasattr(A.Abc,'disassemble') else "\n".join(abc.disassemble(body["code"]))
                    if rx.search(txt):
                        hits = [l.strip() for l in txt.splitlines() if rx.search(l)]
                        print(f"{base} :: {c['name']}.{q}  -> {hits[:4]}")
    except Exception as e:
        pass
