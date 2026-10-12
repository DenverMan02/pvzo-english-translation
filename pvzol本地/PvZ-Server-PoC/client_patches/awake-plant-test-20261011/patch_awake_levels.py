"""Add the configured plant-level requirement without rebuilding Awake classes.

The caller owns asset aliases/fileVer deployment. This module edits bytes only.
"""
from pathlib import Path
import hashlib
import struct
import sys
import zlib

HERE = Path(__file__).resolve().parent
SERVER = HERE.parents[1]
sys.path.insert(0, str(SERVER / "client_patches/tower-fullscreen-item-position-20261010-002217/validation"))
from avm2_validate import BodyReplacement, body_tail, instructions, replace_bodies
from abc_tools import encode_u30, find_abc, parse_abc, s32, u30

CLASS = "com.qq.modules.awake.view.components.AwakeMainWindowMediator"
FACTORY = "com.qq.modules.awake.model.templates"


class Assembler:
    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.fixups = []

    def op(self, opcode, *args):
        self.code.append(opcode)
        for value in args:
            self.code.extend(encode_u30(value))
        return self

    def label(self, name):
        self.labels[name] = len(self.code)
        return self

    def branch(self, opcode, name):
        self.code.append(opcode)
        pos = len(self.code)
        self.code.extend(b"\0" * 3)
        self.fixups.append((pos, name))
        return self

    def finish(self):
        for pos, name in self.fixups:
            delta = self.labels[name] - (pos + 3)
            assert -(1 << 23) <= delta < 1 << 23
            self.code[pos:pos+3] = (delta & 0xffffff).to_bytes(3, "little")
        return bytes(self.code)


def qname(info, index):
    item = info.multinames[index]
    if item and item[0] in (7, 13):
        return info.strings[info.namespaces[item[1]][1]], info.strings[item[2]]
    if item and item[0] in (9, 14):
        return None, info.strings[item[1]]
    return None, None


def mn(info, name, package=None):
    found = [i for i, item in enumerate(info.multinames) if item and
             qname(info, i)[1] == name and (package is None or qname(info, i)[0] == package)]
    assert len(found) == 1, (name, package, found)
    return found[0]


def pool_spans(abc):
    pos = 4
    for _ in range(2):
        count, pos = u30(abc, pos)
        for _ in range(max(0, count-1)):
            pos = s32(abc, pos)
    count, pos = u30(abc, pos)
    pos += max(0, count-1)*8
    string_start = pos
    count, pos = u30(abc, pos)
    string_entries = pos
    for _ in range(max(0, count-1)):
        size, pos = u30(abc, pos)
        pos += size
    string_end = pos
    count, pos = u30(abc, pos)
    for _ in range(max(0, count-1)):
        _, pos = u30(abc, pos+1)
    count, pos = u30(abc, pos)
    for _ in range(max(0, count-1)):
        size, pos = u30(abc, pos)
        for _ in range(size):
            _, pos = u30(abc, pos)
    multiname_start = pos
    count, pos = u30(abc, pos)
    multiname_entries = pos
    for _ in range(max(0, count-1)):
        kind = abc[pos]
        pos += 1
        size = 2 if kind in (7, 13, 9, 14) else 1 if kind in (15, 16, 27, 28) else 0 if kind in (17, 18) else None
        if kind == 29:
            _, pos = u30(abc, pos)
            size, pos = u30(abc, pos)
        assert size is not None, kind
        for _ in range(size):
            _, pos = u30(abc, pos)
    return (string_start, string_entries, string_end), (multiname_start, multiname_entries, pos)


def add_pool_entries(abc):
    before = parse_abc(abc)
    strings = list(before.strings)
    values = ("level", "升级条件：植物等级 ", "升级条件：已满觉醒", "升级条件：无法觉醒", "16")
    for value in values:
        if value not in strings:
            strings.append(value)
    (start, _, end), _ = pool_spans(abc)
    encoded = bytearray(encode_u30(len(strings)))
    for value in strings[1:]:
        raw = value.encode("utf-8")
        encoded.extend(encode_u30(len(raw)))
        encoded.extend(raw)
    changed = abc[:start] + bytes(encoded) + abc[end:]
    info = parse_abc(changed)
    if not any(qname(info, i) == ("", "level") for i in range(1, len(info.multinames))):
        public_namespace = info.multinames[mn(info, "width", "")][1]
        _, (start, entries, end) = pool_spans(changed)
        encoded = (encode_u30(len(info.multinames)+1) + changed[entries:end] +
                   b"\x07" + encode_u30(public_namespace) + encode_u30(strings.index("level")))
        changed = changed[:start] + encoded + changed[end:]
    after = parse_abc(changed)
    assert after.strings[:len(before.strings)] == before.strings
    assert after.multinames[:len(before.multinames)] == before.multinames
    assert after.namespaces == before.namespaces
    assert after.class_methods == before.class_methods
    return changed


def config(a, info, vo):
    """Fetch the same star-five source transition used by the original UI."""
    a.op(0x60, mn(info, "AwakeFactory", FACTORY)).op(0x66, mn(info, "instance", ""))
    a.op(0x5d, mn(info, "int", ""))
    vo(a)
    a.op(0x66, mn(info, "cardId", "")).op(0x24, 10).op(0xa3)
    a.op(0x46, mn(info, "int", ""), 1).op(0x24, 10).op(0xa2).op(0x24, 5).op(0xa0)
    a.op(0x46, mn(info, "getAwakeConfigByCardId", ""), 1)
    a.op(0x80, mn(info, "AwakeConfigCE", "com.qq.modules.awake.model.templates.ce"))


def patch_awake_levels(packed):
    assert packed[:3] in (b"CWS", b"FWS")
    raw = packed[:8] + zlib.decompress(packed[8:]) if packed[:3] == b"CWS" else packed
    assert len(raw) == int.from_bytes(raw[4:8], "little")
    begin, end, length_at, header_size, tag_length = find_abc(raw)
    assert header_size == 6
    original = raw[begin:end]
    before = parse_abc(original)
    abc = add_pool_entries(original)
    info = parse_abc(abc)
    string = lambda value: info.strings.index(value)
    cur = lambda a: a.op(0xd0).op(0x66, mn(info, "m_curVO"))
    view_heading = lambda a: a.op(0xd0).op(0x66, mn(info, "view", "")).op(0x66, mn(info, "l_w_levelupCondition", ""))
    targets = {}
    offsets = {}

    # Append to the sole terminal return so original control flow, loop locals,
    # scope and all attribute/feature creation remain intact.
    mid = info.class_methods[(CLASS, "updateBaseInfo")]
    _, code_at, size = info.method_bodies[mid]
    code = abc[code_at:code_at+size]
    assert code[-1] == 0x47 and sum(i.opcode == 0x47 for i in instructions(code).values()) == 1
    header = info.method_headers[mid]
    local = header[2]
    a = Assembler()
    config(a, info, cur)
    a.op(0x63, local)
    view_heading(a).op(0x25, 292).op(0x61, mn(info, "width", ""))
    view_heading(a).op(0x2c, string("fontSize")).op(0x2c, string("16")).op(0x4f, mn(info, "SetStyle", ""), 2)
    a.op(0x62, local).branch(0x12, "unavailable")
    view_heading(a).op(0x2c, string("升级条件：植物等级 "))
    cur(a).op(0x66, mn(info, "level", "")).op(0xa0).op(0x2c, string("/")).op(0xa0)
    a.op(0x62, local).op(0x66, mn(info, "cardLevel", "")).op(0xa0).op(0x61, mn(info, "text", ""))
    cur(a).op(0x66, mn(info, "level", ""))
    a.op(0x62, local).op(0x66, mn(info, "cardLevel", "")).branch(0x15, "short")
    view_heading(a).op(0x2c, string("color")).op(0x2c, string("#ffcc33")).op(0x4f, mn(info, "SetStyle", ""), 2)
    a.branch(0x10, "end")
    a.label("short")
    view_heading(a).op(0x2c, string("color")).op(0x2c, string("#ff0000")).op(0x4f, mn(info, "SetStyle", ""), 2)
    a.branch(0x10, "end")
    a.label("unavailable")
    cur(a).op(0x66, mn(info, "awakeLevel", "")).op(0x24, 5).branch(0x15, "unknown")
    view_heading(a).op(0x2c, string("升级条件：已满觉醒")).op(0x61, mn(info, "text", ""))
    a.branch(0x10, "neutral")
    a.label("unknown")
    view_heading(a).op(0x2c, string("升级条件：无法觉醒")).op(0x61, mn(info, "text", ""))
    a.label("neutral")
    view_heading(a).op(0x2c, string("color")).op(0x2c, string("#ffcc33")).op(0x4f, mn(info, "SetStyle", ""), 2)
    a.label("end").op(0x47)
    offsets["updateBaseInfo"] = len(code)-1
    targets[mid] = BodyReplacement(code[:-1] + a.finish(), max_stack=max(header[1], 8), local_count=local+1)

    # Preserve original material/forge/star decisions, then add the missing
    # level prerequisite. This runs even for tiles that ignore material cost.
    mid = info.class_methods[(CLASS, "isCardAwakeEnable")]
    _, code_at, size = info.method_bodies[mid]
    code = abc[code_at:code_at+size]
    assert code[-2:] == b"\xd3\x48"
    a = Assembler()
    config(a, info, lambda a: a.op(0xd1))
    a.op(0x63, 4).op(0x62, 4).branch(0x12, "denied")
    a.op(0xd1).op(0x66, mn(info, "level", "")).op(0x62, 4).op(0x66, mn(info, "cardLevel", "")).branch(0x15, "denied")
    a.op(0xd3).op(0x48)
    a.label("denied").op(0x27).op(0x48)
    offsets["isCardAwakeEnable"] = len(code)-2
    targets[mid] = BodyReplacement(code[:-2] + a.finish(), max_stack=max(info.method_headers[mid][1], 8))

    # Direct activation also checks the condition, so a stale enabled button
    # cannot send an invalid awakening request after a card update.
    mid = info.class_methods[(CLASS, "onAwake")]
    _, code_at, size = info.method_bodies[mid]
    code = abc[code_at:code_at+size]
    assert code[:2] == b"\xd0\x30" and code[-1] == 0x47
    a = Assembler().op(0xd0).op(0x30).op(0xd0)
    cur(a).op(0x27).op(0x46, mn(info, "isCardAwakeEnable"), 2).branch(0x12, "end")
    a.code.extend(code[2:-1])
    a.label("end").op(0x47)
    targets[mid] = BodyReplacement(a.finish(), max_stack=max(info.method_headers[mid][1], 4))

    changed, reports = replace_bodies(abc, targets)
    after = parse_abc(changed)
    assert after.class_methods == before.class_methods
    assert after.namespaces == before.namespaces
    assert after.multinames[:len(before.multinames)] == before.multinames
    for other in before.method_bodies:
        if other in targets:
            assert after.method_headers[other][3:] == before.method_headers[other][3:]
            continue
        old_start = before.method_headers[other][0]
        _, _, old_end = body_tail(original, before, other)
        new_start = after.method_headers[other][0]
        _, _, new_end = body_tail(changed, after, other)
        assert original[old_start:old_end] == changed[new_start:new_end], other
    result = bytearray(raw[:begin] + changed + raw[end:])
    struct.pack_into("<I", result, length_at, tag_length+len(changed)-len(original))
    struct.pack_into("<I", result, 4, len(result))
    result[:3] = b"CWS"
    patched = bytes(result[:8]) + zlib.compress(bytes(result[8:]), 9)
    manifest = dict(original_sha256=hashlib.sha256(packed).hexdigest(),
                    patched_sha256=hashlib.sha256(patched).hexdigest(),
                    methods=[dict(classname=c, name=m, id=idx, validation=reports[idx])
                             for (c, m), idx in info.class_methods.items() if idx in targets],
                    appended_strings=after.strings[len(before.strings):],
                    appended_multinames=len(after.multinames)-len(before.multinames),
                    unchanged_method_count=len(before.method_bodies)-len(targets),
                    insertion_offsets=offsets, heading_bounds=dict(x=361, y=263, width=292, fontSize=16))
    return patched, manifest


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    packed = args.source.read_bytes()
    patched, manifest = patch_awake_levels(packed)
    args.destination.write_bytes(patched)
    args.destination.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
