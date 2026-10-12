import importlib.util, sys, glob, os, re
spec = importlib.util.spec_from_file_location("abcadis", "PvZ-Server-PoC/client_tools/abcdis.py")
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
needle = sys.argv[1]
for path in sorted(glob.glob("webroot/*.swf")):
    base = os.path.basename(path)
    if re.search(r"_[0-9a-f]{32}\.swf$", base):
        continue
    try:
        for abc in A.load_swf(path):
            for c in abc.classes:
                if needle.lower() in c["name"].lower():
                    print(base, "::", c["name"])
    except Exception as e:
        pass
