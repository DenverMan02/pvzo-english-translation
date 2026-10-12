"""Throwaway: dump any ABC method body whose bytecode mentions a symbol, with const values resolved."""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abcdis", os.path.join(HERE, "client_tools", "abcdis.py"))
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

path = sys.argv[1]; needle = sys.argv[2]
for abc in A.load_swf(path):
    for midx, body in sorted(abc.body_of_method.items()):
        code = body["code"]
        out, pos, n = [], 0, len(code)
        while pos < n:
            op = code[pos]; pos += 1
            name, fmt = A.OPS.get(op, ("op_%#02x" % op, "?"))
            arg = ""
            try:
                if fmt in ("m", "s", "i", "u", "d"):
                    r = A.Reader(code, pos); idx = r.u30(); pos = r.pos
                    if fmt == "m": arg = abc.mn(idx)
                    elif fmt == "s": arg = repr(abc.strings[idx])
                    elif fmt == "i": arg = str(abc.ints[idx])
                    elif fmt == "u": arg = str(abc.uints[idx])
                    else: arg = repr(abc.doubles[idx])
                elif fmt == "b":
                    arg = str(code[pos]); pos += 1
                elif fmt == "j":
                    r = A.Reader(code, pos); arg = "%d" % r.s24(); pos = r.pos
            except Exception as exc:
                arg = "<%s>" % exc
            out.append("      %s %s" % (name, arg))
        txt = "\n".join(out)
        if needle.lower() in txt.lower():
            print("=== method %d" % midx)
            print(txt); print()
