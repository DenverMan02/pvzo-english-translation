"""Minimal ActionScript 3 (ABC) reader/disassembler for the local PvZOL build.

Used to answer questions like "which request does this button actually send?"
when the server log alone is not enough.  Two entry points:

    python client_tools/abcdis.py <swf> --classes "<substr>"
    python client_tools/abcdis.py <swf> --strings "<regex>"

ABC files produced by the ASC compiler frequently have a few bytes of *drift*
in their tail sections (instance_info / class_info / script_info /
method_body_info), so a straight sequential parse usually dies with
``trait kind N`` or yields zero method bodies.  ``Abc.load`` therefore retries
``parse_tail`` around the drifted position and keeps the candidate that both
finishes exactly at the end of the blob and yields the most bodies.
"""
from __future__ import annotations

import io
import re
import struct
import sys
import zlib

# --------------------------------------------------------------------------
# SWF containers
# --------------------------------------------------------------------------
TAG_DO_ABC = 82
TAG_DO_ABC_DEFINE = 72


def swf_body(path: str) -> bytes:
    raw = open(path, "rb").read()
    if raw[:3] == b"CWS":
        return zlib.decompressobj().decompress(raw[8:])
    return raw[8:]


def abc_blobs(path: str):
    """Yield every DoABC payload of a SWF as bytes."""
    data = swf_body(path)
    pos, version_pos = 0, 8
    _ = version_pos
    # re-read a proper RECT-free offset: body starts right after the header.
    # Layout after signature+version(1)+filelength(4): RECT, u16 framerate, u16 framecount.
    bitpos = (data[0] >> 3) * 4 + 5  # nbits*5 + 5 nibbles... computed below instead
    nb = data[0] >> 3
    bitpos = int((5 + nb * 4) / 8) + (1 if (5 + nb * 4) % 8 else 0)
    pos = 8 + bitpos + 4
    out = []
    while pos + 2 <= len(data):
        tag_and_len = struct.unpack_from("<H", data, pos)[0]
        tag, length = tag_and_len >> 6, tag_and_len & 0x3F
        pos += 2
        if length == 0x3F:
            if pos + 4 > len(data):
                break
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        if length < 0 or pos + length > len(data):
            break
        payload = data[pos:pos + length]
        pos += length
        if tag in (TAG_DO_ABC, TAG_DO_ABC_DEFINE):
            if len(payload) < 4:
                continue
            flags = struct.unpack_from("<I", payload, 0)[0]
            if tag == TAG_DO_ABC_DEFINE:
                name_end = payload.index(b"\x00", 4)
            else:
                name_end = payload.index(b"\x00", 4) if b"\x00" in payload[4:200] else 3
            out.append(payload[name_end + 1:])
            _ = flags
    return out


# --------------------------------------------------------------------------
# opcode table (subset that matters for reading behaviour)
# --------------------------------------------------------------------------
OPS = {
    0x01: ("bkpt", ""), 0x02: ("nop", ""), 0x03: ("throw", ""),
    0x04: ("getsuper", "m"), 0x05: ("setsuper", "m"), 0x06: ("dxns", "s"),
    0x07: ("dxnslate", ""), 0x08: ("kill", "u"), 0x09: ("label", ""),
    0x0C: ("ifnlt", "j"), 0x0D: ("ifnle", "j"), 0x0E: ("ifngt", "j"),
    0x0F: ("ifnge", "j"),
    0x10: ("jump", "j"), 0x11: ("iftrue", "j"), 0x12: ("iffalse", "j"),
    0x13: ("ifeq", "j"), 0x14: ("ifne", "j"), 0x15: ("iflt", "j"),
    0x16: ("ifle", "j"), 0x17: ("ifgt", "j"), 0x18: ("ifge", "j"),
    0x19: ("ifstricteq", "j"), 0x1A: ("ifstrictne", "j"),
    0x1B: ("lookupswitch", "CASE"), 0x1C: ("pushwith", ""),
    0x1D: ("popscope", ""), 0x1E: ("nextname", ""), 0x1F: ("hasnext", ""),
    0x20: ("pushnull", ""), 0x21: ("pushundefined", ""), 0x23: ("nextvalue", ""),
    0x24: ("pushbyte", "b"), 0x25: ("pushshort", "u"), 0x26: ("pushtrue", ""),
    0x27: ("pushfalse", ""), 0x28: ("pushnan", ""), 0x29: ("pop", ""),
    0x2A: ("dup", ""), 0x2B: ("swap", ""), 0x2C: ("pushstring", "s"),
    0x2D: ("pushint", "i"), 0x2E: ("pushuint", "u"), 0x2F: ("pushdouble", "d"),
    0x30: ("pushscope", ""), 0x31: ("pushnamespace", "u"), 0x32: ("hasnext2", "uu"),
    0x40: ("newfunction", "u"), 0x41: ("call", "u"), 0x42: ("construct", "u"),
    0x43: ("callmethod", "um"), 0x44: ("callstatic", "um"),
    0x45: ("callsuper", "mu"), 0x46: ("callproperty", "mu"),
    0x47: ("returnvoid", ""), 0x48: ("returnvalue", ""),
    0x49: ("constructsuper", "u"), 0x4A: ("constructprop", "mu"),
    0x4C: ("callproplex", "mu"), 0x4E: ("callsupervoid", "mu"),
    0x4F: ("callpropvoid", "mu"), 0x53: ("applytype", "u"),
    0x55: ("newobject", "u"), 0x56: ("newarray", "u"), 0x57: ("newactivation", ""),
    0x58: ("newclass", "u"), 0x59: ("getdescendants", "m"),
    0x5A: ("newcatch", "u"),
    0x5D: ("findpropstrict", "m"), 0x5E: ("findproperty", "m"),
    0x5F: ("finddef", "m"),
    0x60: ("getlex", "m"), 0x61: ("setproperty", "m"), 0x62: ("getlocal", "u"),
    0x63: ("setlocal", "u"), 0x64: ("getglobalscope", ""),
    0x65: ("getscopeobject", "b"), 0x66: ("getproperty", "m"),
    0x68: ("initproperty", "m"), 0x6A: ("deleteproperty", "m"),
    0x6C: ("getslot", "u"), 0x6D: ("setslot", "u"),
    0x6E: ("getglobalslot", "u"), 0x6F: ("setglobalslot", "u"),
    0x70: ("convert_s", ""), 0x71: ("esc_xelem", ""), 0x72: ("esc_xattr", ""),
    0x73: ("convert_i", ""), 0x74: ("convert_u", ""), 0x75: ("convert_d", ""),
    0x76: ("convert_b", ""), 0x77: ("convert_o", ""), 0x78: ("checkfilter", ""),
    0x80: ("coerce", "m"), 0x82: ("coerce_a", ""), 0x85: ("coerce_s", ""),
    0x86: ("astype", "m"), 0x87: ("astypelate", ""),
    0x90: ("negate", ""), 0x91: ("increment", ""), 0x92: ("inclocal", "u"),
    0x93: ("decrement", ""), 0x94: ("declocal", "u"), 0x95: ("typeof", ""),
    0x96: ("not", ""), 0x97: ("bitnot", ""),
    0xA0: ("add", ""), 0xA1: ("subtract", ""), 0xA2: ("multiply", ""),
    0xA3: ("divide", ""), 0xA4: ("modulo", ""), 0xA5: ("lshift", ""),
    0xA6: ("rshift", ""), 0xA7: ("urshift", ""), 0xA8: ("bitand", ""),
    0xA9: ("bitor", ""), 0xAA: ("bitxor", ""), 0xAB: ("equals", ""),
    0xAC: ("strictequals", ""), 0xAD: ("lessthan", ""), 0xAE: ("lessequals", ""),
    0xAF: ("greaterthan", ""), 0xB0: ("greaterequals", ""),
    0xB1: ("instanceof", ""), 0xB2: ("istype", "m"), 0xB3: ("istypelate", ""),
    0xB4: ("in", ""),
    0xC0: ("increment_i", ""), 0xC1: ("decrement_i", ""),
    0xC2: ("inclocal_i", "u"), 0xC3: ("declocal_i", "u"),
    0xC4: ("negate_i", ""), 0xC5: ("add_i", ""), 0xC6: ("subtract_i", ""),
    0xC7: ("multiply_i", ""),
    0xD0: ("getlocal0", ""), 0xD1: ("getlocal1", ""), 0xD2: ("getlocal2", ""),
    0xD3: ("getlocal3", ""),
    0xD4: ("setlocal0", ""), 0xD5: ("setlocal1", ""), 0xD6: ("setlocal2", ""),
    0xD7: ("setlocal3", ""),
    0xEF: ("debug", "DEBUG"),
}


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.pos = bytes(data), pos

    def u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u30(self):
        value = shift = 0
        while True:
            byte = self.data[self.pos]
            self.pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value & 0x3FFFFFFF
            shift += 7

    u32 = u30

    def s32(self):
        v = self.u30()
        return v - (1 << 30) if v >= (1 << 29) else v

    def d64(self):
        v = struct.unpack_from("<d", self.data, self.pos)[0]
        self.pos += 8
        return v

    def raw(self, n: int):
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v


class Abc:
    def __init__(self, blob: bytes):
        self.b = blob
        r = Reader(blob)
        self.minor, self.major = r.u16(), r.u16()
        self.ints = [0] * max(1, r.u30())
        for i in range(1, len(self.ints)):
            self.ints[i] = r.s32()
        n = r.u30()
        self.uints = [0] * max(1, n)
        for i in range(1, n):
            self.uints[i] = r.u32()
        n = r.u30()
        self.doubles = [0.0] * max(1, n)
        for i in range(1, n):
            self.doubles[i] = r.d64()
        n = r.u30()
        self.strings = ["*"] * max(1, n)
        for i in range(1, n):
            ln = r.u30()
            self.strings[i] = r.raw(ln).decode("utf-8", "replace")
        n = r.u30()
        self.namespaces = [None] * max(1, n)
        for i in range(1, n):
            self.namespaces[i] = (r.u8(), r.u30())
        n = r.u30()
        self.ns_sets = [None] * max(1, n)
        for i in range(1, n):
            cnt = r.u30()
            self.ns_sets[i] = [r.u30() for _ in range(cnt)]
        n = r.u30()
        self.multinames = [(0, None)] * max(1, n)
        for i in range(1, n):
            kind = r.u8()
            if kind in (0x07, 0x0D):
                self.multinames[i] = (kind, (r.u30(), r.u30()))       # QName, QNameA
            elif kind in (0x0F, 0x10):
                self.multinames[i] = (kind, (r.u30(),))               # RTQName
            elif kind in (0x11, 0x12):
                self.multinames[i] = (kind, ())                       # RTQNameL
            elif kind in (0x09, 0x0E):
                self.multinames[i] = (kind, (r.u30(), r.u30()))       # Multiname
            elif kind in (0x1B, 0x1C):
                self.multinames[i] = (kind, (r.u30(),))               # MultinameL
            elif kind == 0x1D:
                self.multinames[i] = (kind, (r.u30(), [r.u30() for _ in range(r.u30())]))
            else:
                raise ValueError(f"bad multiname kind {kind:#x} at {r.pos}")
        self.pool_pos = r.pos
        n = r.u30()
        self.methods = [None] * max(1, n)
        for i in range(1, n):
            pc, ret = r.u30(), r.u30()
            params = [r.u30() for _ in range(pc)]
            name = r.u30()
            flags = r.u8()
            if flags & 0x08:                       # OPTIONAL: (u30 value, u8 kind)*
                for _ in range(r.u30()):
                    r.u30()
                    r.u8()
            if flags & 0x80:                       # PARAM_NAMES: u30 * param_count
                for _ in range(pc):
                    r.u30()
            self.methods[i] = {"name": self.strings[name], "params": pc}
        n = r.u30()
        self.metadata = []
        for _ in range(n):
            self.metadata.append(r.u30())
            for _ in range(r.u30()):
                r.u30(), r.u30()
        self.tail_pos = r.pos
        self.classes, self.bodies = [], []
        self.body_of_method = {}

    # ---- names ----
    def s(self, i):
        return self.strings[i] if 0 <= i < len(self.strings) else f"?{i}"

    def mn(self, i):
        if i == 0 or i >= len(self.multinames):
            return "*"
        kind, data = self.multinames[i]
        if kind in (0x07, 0x0D, 0x09, 0x0E):
            ns, name = data
            nsp = self.namespaces[ns] if ns < len(self.namespaces) else None
            prefix = self.s(nsp[1]) + "::" if nsp and nsp[1] else ""
            return prefix + self.s(name)
        if kind in (0x0F, 0x10, 0x11, 0x12, 0x1B, 0x1C):
            return "<runtime>"
        if kind == 0x1D:
            return self.mn(data[0]) + "<...>"
        return f"?mn{i}"

    # ---- tail parsing with drift repair ----
    def _traits(self, r):
        out = []
        for _ in range(r.u30()):
            name = r.u30()
            k = r.u8()
            tag = k & 0x0F
            if k & 0x40:
                for _ in range(r.u30()):
                    r.u30()
            entry = {"name": self.mn(name), "kind": tag}
            if tag in (0x00, 0x06):
                entry["slot"], entry["type"] = r.u30(), r.u30()
                v = r.u30()
                if v:
                    entry["vkind"] = r.u8()
            elif tag in (0x01, 0x02, 0x03):
                entry["disp"], entry["method"] = r.u30(), r.u30()
            elif tag == 0x04:
                entry["slot"], entry["class"] = r.u30(), r.u30()
            elif tag == 0x05:
                entry["slot"], entry["method"] = r.u30(), r.u30()
            else:
                raise ValueError(f"trait kind {tag}")
            out.append(entry)
        return out

    def parse_tail(self, pos):
        r = Reader(self.b, pos)
        class_count = r.u30()
        instances = []
        for _ in range(class_count):
            name, sup = r.u30(), r.u30()
            flags = r.u8()
            if flags & 0x08:
                r.u30()
            for _ in range(r.u30()):
                r.u30()
            iinit = r.u30()
            instances.append({"name": self.mn(name), "super": self.mn(sup),
                              "traits": self._traits(r), "iinit": iinit})
        classes = []
        for inst in instances:
            cinit = r.u30()
            classes.append({"name": inst["name"], "traits": self._traits(r),
                            "iinit": inst["iinit"], "cinit": cinit,
                            "super": inst["super"], "inst_traits": inst["traits"]})
        for _ in range(r.u30()):  # script_info
            r.u30()
            self._traits(r)
        bodies = []
        for _ in range(r.u30()):
            m = r.u30()
            ms, lc, isd, msd = r.u30(), r.u30(), r.u30(), r.u30()
            ln = r.u30()
            code = r.raw(ln)
            for _ in range(r.u30()):
                r.u30(), r.u30(), r.u30(), r.u30(), r.u30()
            bodies.append({"method": m, "code": code, "local_count": lc,
                           "traits": self._traits(r)})
        return classes, bodies, r.pos

    def load(self, tries=1200):
        best = None
        for delta in range(0, tries):
            for p in ({self.tail_pos - delta, self.tail_pos + delta}
                      if delta else {self.tail_pos}):
                if p < self.pool_pos or p >= len(self.b):
                    continue
                try:
                    classes, bodies, end = self.parse_tail(p)
                except Exception:
                    continue
                if end == len(self.b) and (best is None or len(bodies) > len(best[1])):
                    best = (classes, bodies, end)
            if best and best[1] and delta > 4:
                break
        if not best:
            raise ValueError("could not recover ABC tail (drift too large)")
        self.classes, self.bodies, _ = best
        self.body_of_method = {b["method"]: b for b in self.bodies}
        return self

    # ---- queries ----
    def class_names(self):
        seen, out = set(), []
        for c in self.classes:
            if c["name"] not in seen:
                seen.add(c["name"])
                out.append(c["name"])
        return sorted(out)

    def trait_methods(self, cls, needle=""):
        """[(qualified, method_index)] for every instance trait whose name matches."""
        out = []
        for t in cls["traits"] + cls["inst_traits"]:
            if "method" not in t or not isinstance(t["name"], str):
                continue
            if needle and needle.lower() not in t["name"].lower():
                continue
            out.append((t["name"], t["method"]))
        return sorted(set(out))

    def disassemble(self, code):
        lines, pos, n = [], 0, len(code)
        while pos < n:
            op = code[pos]
            start = pos
            pos += 1
            name, fmt = OPS.get(op, (f"op_{op:#02x}", "?"))
            args = []
            try:
                if fmt in ("m", "s", "i", "u", "d", "b"):
                    idx = (Reader(code, pos).u30() if fmt in ("m", "s", "i", "u")
                           else (code[pos] if fmt == "b" else None))
                    if fmt == "m":
                        args.append(self.mn(idx))
                    elif fmt == "s":
                        args.append(repr(self.s(idx)))
                    elif fmt == "i":
                        args.append(str(self.ints[idx] if idx < len(self.ints) else idx))
                    elif fmt == "u":
                        args.append(str(self.uints[idx] if idx < len(self.uints) else idx))
                    elif fmt == "b":
                        args.append(str(code[pos]))
                    elif fmt == "d":
                        args.append(str(self.doubles[Reader(code, pos).u30()]))
                    nbytes = len(code[start + 1:]) - len(code[pos:]) if fmt != "d" else -1
                    if fmt == "b":
                        pos += 1
                    elif fmt == "d":
                        pos += Reader(code, pos).u30() and 0
                        r2 = Reader(code, pos)
                        r2.u30()
                        pos = r2.pos
                    else:
                        r2 = Reader(code, pos)
                        r2.u30()
                        pos = r2.pos
                elif fmt == "j":
                    r2 = Reader(code, pos)
                    s24 = int.from_bytes(code[pos:pos + 3], "little", signed=True)
                    r2.u30()
                    pos = r2.pos
                    args.append(f"-> {start + s24 if False else pos + s24}")
                elif fmt == "CASE":
                    r2 = Reader(code, pos)
                    r2.u30()
                    cnt = r2.u30()
                    for _ in range(cnt + 1):
                        r2.u30()
                    pos = r2.pos
                    args.append(f"{cnt} cases")
                elif fmt == "DEBUG":
                    r2 = Reader(code, pos)
                    r2.u8()
                    r2.u30()
                    r2.u8()
                    r2.u30()
                    pos = r2.pos
                elif fmt == "um":
                    r2 = Reader(code, pos)
                    v = r2.u30()
                    ml = r2.u30()
                    args.append(f"{v}, {self.mn(ml)}")
                    pos = r2.pos
                elif fmt == "mu":
                    r2 = Reader(code, pos)
                    ml = r2.u30()
                    v = r2.u30()
                    args.append(f"{self.mn(ml)}, {v}")
                    pos = r2.pos
                elif fmt == "uu":
                    r2 = Reader(code, pos)
                    args.append(f"{r2.u30()}, {r2.u30()}")
                    pos = r2.pos
            except Exception:
                args.append("<?>")
            lines.append(f"  {start:5d}  {name:16s} {' '.join(str(a) for a in args)}")
        return lines


def _ulen(r):
    start = r.pos
    r.u30()
    return r.pos - start


def load_swf(path):
    blobs = abc_blobs(path)
    abcs = []
    for blob in blobs:
        try:
            abcs.append(Abc(blob).load())
        except Exception as exc:
            print(f"  ! ABC blob skipped: {exc}")
    return abcs


def main(argv):
    path = argv[1] if len(argv) > 1 else "webroot/ModuleEndlessFuben.swf"
    abcs = load_swf(path)
    print(f"{path}: {len(abcs)} ABC block(s)")
    for abc in abcs:
        print(f"  classes={len(abc.classes)} bodies={len(abc.bodies)}")
    if "--classes" in argv:
        needle = argv[argv.index("--classes") + 1]
        for abc in abcs:
            for name in abc.class_names():
                if needle.lower() in name.lower():
                    print("   ", name)
    if "--methods" in argv:
        parts = argv[argv.index("--methods") + 1:]
        cls, method = (parts + [""])[:2]
        for abc in abcs:
            for c in abc.classes:
                if cls.lower() not in c["name"].lower():
                    continue
                for qname, midx in abc.trait_methods(c, method):
                    body = abc.body_of_method.get(midx)
                    print(f"\n=== {c['name']}.{qname}  (method {midx})")
                    if not body:
                        print("    <no body>")
                        continue
                    print("\n".join(abc.disassemble(body["code"])))


if __name__ == "__main__":
    main(sys.argv)
