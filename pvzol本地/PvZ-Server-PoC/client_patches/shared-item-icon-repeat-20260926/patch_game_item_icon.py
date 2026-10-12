#!/usr/bin/env python3
"""Remove the two NaN dimension writes from BagItemIcon.resetIcon in Game_p."""

from __future__ import annotations

import argparse
import hashlib
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "card-exp-bean-repeat-20260926"))
from patch_module_card_clickthrough import encode_u30, find_abc, parse_abc  # noqa: E402


def patch_file(source: Path, destination: Path) -> tuple[str, str]:
    packed = source.read_bytes()
    if packed[:3] == b"CWS":
        raw = bytearray(packed[:8] + zlib.decompress(packed[8:]))
    elif packed[:3] == b"FWS":
        raw = bytearray(packed)
    else:
        raise ValueError("expected an FWS/CWS SWF")

    abc_start, abc_end, *_ = find_abc(raw)
    abc = bytes(raw[abc_start:abc_end])
    info = parse_abc(abc)
    target = ("com.qq.modules.bag.view.components.BagItemIcon", "resetIcon")
    method_id = info.class_methods.get(target)
    if method_id is None:
        raise ValueError(f"missing method {target}")
    _, code_offset, code_length = info.method_bodies[method_id]
    code = abc[code_offset:code_offset + code_length]

    def named_indexes(name: str, kinds: tuple[int, ...]) -> list[int]:
        found = []
        for index, entry in enumerate(info.multinames):
            if not entry or entry[0] not in kinds:
                continue
            name_index = entry[2] if entry[0] in (7, 13) else entry[1]
            if name_index < len(info.strings) and info.strings[name_index] == name:
                found.append(index)
        return found

    icon_names = named_indexes("_itemIcon", (7, 13))
    nan_names = named_indexes("NaN", (7, 9, 13, 14))
    width_names = named_indexes("width", (7, 13))
    height_names = named_indexes("height", (7, 13))
    matches = []
    for icon in icon_names:
        for nan in nan_names:
            for width in width_names:
                for height in height_names:
                    prefix = b"\xd0\x66" + encode_u30(icon) + b"\x60" + encode_u30(nan)
                    writes = prefix + b"\x61" + encode_u30(width) + prefix + b"\x61" + encode_u30(height)
                    if code.count(writes) == 1:
                        matches.append((code.index(writes), writes))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one consecutive NaN width/height pair, found {len(matches)}")

    position, writes = matches[0]
    absolute = abc_start + code_offset + position
    if raw[absolute:absolute + len(writes)] != writes:
        raise ValueError("unexpected resetIcon bytecode")
    # NOP keeps every ABC offset and SWF tag length unchanged. The original
    # x/y/scale reset remains, as do equipment-specific 50 x 50 assignments.
    raw[absolute:absolute + len(writes)] = b"\x02" * len(writes)
    if packed[:3] == b"CWS":
        output = bytes(raw[:8]) + zlib.compress(bytes(raw[8:]), level=9)
    else:
        output = bytes(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)
    return hashlib.md5(packed).hexdigest(), hashlib.md5(output).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    old_hash, new_hash = patch_file(args.source, args.destination)
    print(f"{args.destination}: {old_hash} -> {new_hash}")
