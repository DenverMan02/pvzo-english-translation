#!/usr/bin/env python3
"""Align 62x62 equipment PNGs with their 62x62 display slots."""

from __future__ import annotations

import hashlib
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "card-exp-bean-repeat-20260926"))
from patch_module_card_clickthrough import encode_u30, find_abc, parse_abc  # noqa: E402


def _property_index(info, name: str, opcode: bytes, code: bytes) -> int:
    """Pick the QName used by this method for a public display property."""
    found = []
    for index, entry in enumerate(info.multinames):
        if not entry or entry[0] not in (7, 13) or info.strings[entry[2]] != name:
            continue
        if opcode + encode_u30(index) in code:
            found.append(index)
    if len(found) != 1:
        raise ValueError(f"expected one used {name} QName, got {found}")
    return found[0]


def _write(icon: int, value: int, prop: int) -> bytes:
    return b"\xd0\x66" + encode_u30(icon) + b"\x24" + bytes([value]) + b"\x61" + encode_u30(prop)


def patch_swf(source: Path, destination: Path, kind: str) -> tuple[str, str]:
    packed = source.read_bytes()
    if packed[:3] == b"CWS":
        raw = bytearray(packed[:8] + zlib.decompress(packed[8:]))
    elif packed[:3] == b"FWS":
        raw = bytearray(packed)
    else:
        raise ValueError("expected an FWS/CWS SWF")

    abc_start, abc_end, tag_length_at, header_length, tag_length = find_abc(raw)
    if header_length != 6:
        raise ValueError("expected long DoABC tag")
    abc = bytes(raw[abc_start:abc_end])
    info = parse_abc(abc)
    if kind == "game":
        target = ("com.qq.modules.bag.view.components.BagItemIcon", "setEquipTemplate")
    elif kind == "card":
        target = ("com.qq.modules.equipment.view.components.EquipmentIcon", "InitializeComponent")
    else:
        raise ValueError(kind)
    method = info.class_methods.get(target)
    if method is None:
        raise ValueError(f"missing method {target}")
    length_at, code_at, code_length = info.method_bodies[method]
    code = abc[code_at:code_at + code_length]

    if kind == "game":
        icon_candidates = [i for i, m in enumerate(info.multinames)
                           if m and m[0] in (7, 13) and info.strings[m[2]] == "_itemIcon"
                           and b"\xd0\x66" + encode_u30(i) in code]
        if len(icon_candidates) != 1:
            raise ValueError(f"unexpected item icon reference: {icon_candidates}")
        icon = icon_candidates[0]
        width = _property_index(info, "width", b"\x61", code)
        height = _property_index(info, "height", b"\x61", code)
        # The x/y properties used by the same icon in resetIcon are the
        # public display properties; disambiguate other x/y namespaces there.
        reset_method = info.class_methods[(target[0], "resetIcon")]
        _, reset_at, reset_length = info.method_bodies[reset_method]
        reset = abc[reset_at:reset_at + reset_length]
        x = _property_index(info, "x", b"\x61", reset)
        y = _property_index(info, "y", b"\x61", reset)
        old = _write(icon, 50, width) + _write(icon, 50, height)
        new = (_write(icon, 62, width) + _write(icon, 62, height)
               + _write(icon, 0, x) + _write(icon, 0, y))
    else:
        icon_candidates = [i for i, m in enumerate(info.multinames)
                           if m and m[0] in (7, 13) and info.strings[m[2]] == "m_icon"
                           and b"\xd0\x66" + encode_u30(i) in code]
        # The method uses the private-slot multiname; other occurrences may
        # describe unrelated symbols in the constant pool.
        if len(icon_candidates) != 1:
            raise ValueError(f"unexpected equipment icon reference: {icon_candidates}")
        icon = icon_candidates[0]
        width = _property_index(info, "width", b"\x61", code)
        height = _property_index(info, "height", b"\x61", code)
        x = _property_index(info, "x", b"\x61", code)
        y = _property_index(info, "y", b"\x61", code)
        old = (_write(icon, 50, width) + _write(icon, 50, height)
               + _write(icon, 6, x) + _write(icon, 6, y))
        new = (_write(icon, 62, width) + _write(icon, 62, height)
               + _write(icon, 0, x) + _write(icon, 0, y))

    if code.count(old) != 1:
        raise ValueError(f"expected exactly one equipment layout block, found {code.count(old)}")
    offset = code.index(old)
    absolute = abc_start + code_at + offset
    raw[absolute:absolute + len(old)] = new
    delta = len(new) - len(old)
    if delta:
        old_length = encode_u30(code_length)
        new_length = encode_u30(code_length + delta)
        absolute_length = abc_start + length_at
        if raw[absolute_length:absolute_length + len(old_length)] != old_length:
            raise ValueError("unexpected code length")
        raw[absolute_length:absolute_length + len(old_length)] = new_length
        struct.pack_into("<I", raw, tag_length_at,
                         tag_length + delta + len(new_length) - len(old_length))
        struct.pack_into("<I", raw, 4, len(raw))

    if packed[:3] == b"CWS":
        output = bytes(raw[:8]) + zlib.compress(bytes(raw[8:]), level=9)
    else:
        output = bytes(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)
    return hashlib.md5(packed).hexdigest(), hashlib.md5(output).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: patch_equipment_icons.py game|card input.swf output.swf")
    before, after = patch_swf(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[1])
    print(f"{sys.argv[1]}: {before} -> {after}")
