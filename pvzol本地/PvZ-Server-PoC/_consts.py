"""Throwaway: resolve AS3 static-constant numeric values (abcdis does not print const values)."""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abcdis", os.path.join(HERE, "client_tools", "abcdis.py"))
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

path = sys.argv[1]
needle = sys.argv[2] if len(sys.argv) > 2 else "CONST_TRADE_COST"
cls_filter = sys.argv[3] if len(sys.argv) > 3 else ""


def decode(abc, code):
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
        out.append("%s %s" % (name, arg))
    return "\n".join(out)


for abc in A.load_swf(path):
    for c in abc.classes:
        if cls_filter and cls_filter.lower() not in c["name"].lower():
            continue
        for qname, midx in abc.trait_methods(c, ""):
            body = abc.body_of_method.get(midx)
            if not body:
                continue
            txt = decode(abc, body["code"])
            if needle.lower() in txt.lower():
                print("=== %s.%s (method %d)" % (c["name"], qname, midx))
                print(txt)
                print()
