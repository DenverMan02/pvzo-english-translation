import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abcdis", os.path.join(HERE, "client_tools", "abcdis.py"))
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)
path, want = sys.argv[1], int(sys.argv[2])
for abc in A.load_swf(path):
    body = abc.body_of_method.get(want)
    if not body: continue
    code = body["code"]; pos, n = 0, len(code)
    while pos < n:
        op = code[pos]; pos += 1
        name, fmt = A.OPS.get(op, ("op_%#02x" % op, "?"))
        arg = ""
        try:
            if fmt in ("m", "s", "i", "u", "d"):
                r = A.Reader(code, pos); idx = r.u30(); pos = r.pos
                arg = {"m": lambda: abc.mn(idx), "s": lambda: repr(abc.strings[idx]),
                       "i": lambda: str(abc.ints[idx]), "u": lambda: str(abc.uints[idx]),
                       "d": lambda: repr(abc.doubles[idx])}[fmt]()
            elif fmt == "b": arg = str(code[pos]); pos += 1
            elif fmt == "j":
                r = A.Reader(code, pos); arg = "%d" % r.s24(); pos = r.pos
        except Exception as exc: arg = "<%s>" % exc
        print("      %s %s" % (name, arg))
