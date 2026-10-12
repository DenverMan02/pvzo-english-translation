"""_findmethod.py <swf> <methodName> -- list classes defining that method."""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("abcadis", "PvZ-Server-PoC/client_tools/abcdis.py")
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
path, want = sys.argv[1], sys.argv[2]
for abc in A.load_swf(path):
    for c in abc.classes:
        for q, midx in abc.trait_methods(c, ""):
            if q.lstrip(":") == want:
                print(c["name"], "->", q, "method", midx)
