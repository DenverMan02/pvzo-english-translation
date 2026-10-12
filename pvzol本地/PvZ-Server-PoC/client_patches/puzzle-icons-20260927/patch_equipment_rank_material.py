#!/usr/bin/env python3
"""Keep equipment-rank material art small and at (0, 0), including after setTemplate."""

from __future__ import annotations

import hashlib
import struct
import sys
import zlib
from pathlib import Path

helper_dir = Path(__file__).resolve().parents[1] / "equipment-icon-alignment-20260926"
sys.path.insert(0, str(helper_dir))
import patch_equipment_icons as patch_helpers  # noqa: E402


def _unique_name_index(info, name: str) -> int:
    matches = [i for i, entry in enumerate(info.multinames)
               if entry and entry[0] in (7, 13) and info.strings[entry[2]] == name]
    if len(matches) != 1:
        raise ValueError(f"expected one QName for {name!r}, got {matches}")
    return matches[0]


def patch(source: Path, destination: Path) -> tuple[str, str]:
    packed = source.read_bytes()
    if packed[:3] == b"CWS":
        raw = bytearray(packed[:8] + zlib.decompress(packed[8:]))
    elif packed[:3] == b"FWS":
        raw = bytearray(packed)
    else:
        raise ValueError("expected an FWS/CWS SWF")

    abc_start, abc_end, tag_length_at, header_length, tag_length = patch_helpers.find_abc(raw)
    if header_length != 6:
        raise ValueError("expected long DoABC tag")
    abc = bytes(raw[abc_start:abc_end])
    info = patch_helpers.parse_abc(abc)
    class_name = "com.qq.modules.equipment.view.components.EquipmentRankView"
    methods = {}
    for method_name in ("InitializeComponent", "updateMaterial"):
        target = (class_name, method_name)
        method = info.class_methods.get(target)
        if method is None:
            raise ValueError(f"missing method {target}")
        code_length_at, code_at, code_length = info.method_bodies[method]
        code = abc[code_at:code_at + code_length]
        if not code.endswith(b"\x47"):
            raise ValueError(f"{method_name} does not end in returnvoid")
        methods[method_name] = (code_length_at, code_at, code)

    material = _unique_name_index(info, "m_materialIcon")
    item_icon = _unique_name_index(info, "itemIcon")
    width = _unique_name_index(info, "width")
    height = _unique_name_index(info, "height")
    init_code = methods["InitializeComponent"][2]
    x = patch_helpers._property_index(info, "x", b"\x61", init_code)
    y = patch_helpers._property_index(info, "y", b"\x61", init_code)

    def set_material_icon(prop: int, value: int) -> bytes:
        return (b"\xd0\x66" + patch_helpers.encode_u30(material)
                + b"\x66" + patch_helpers.encode_u30(item_icon)
                + b"\x24" + bytes([value]) + b"\x61" + patch_helpers.encode_u30(prop))

    legacy = (set_material_icon(width, 50) + set_material_icon(height, 50)
              + set_material_icon(x, 6) + set_material_icon(y, 6))
    corrected = (set_material_icon(width, 40) + set_material_icon(height, 40)
                 + set_material_icon(x, 0) + set_material_icon(y, 0))
    if init_code.count(legacy) > 1:
        raise ValueError("more than one legacy material-icon block")
    if legacy in init_code:
        new_init = init_code.replace(legacy, corrected, 1)
    elif corrected in init_code:
        new_init = init_code
    else:
        new_init = init_code[:-1] + corrected + init_code[-1:]

    # BagItemIcon.setTemplate invokes resetIcon(), which moves itemIcon back to (6, 6).
    # Run this block at the end of updateMaterial so every new material is aligned again.
    update_code = methods["updateMaterial"][2]
    new_update = (update_code if corrected in update_code
                  else update_code[:-1] + corrected + update_code[-1:])

    new_abc = bytearray(abc)
    replacements = ((methods["InitializeComponent"], new_init),
                    (methods["updateMaterial"], new_update))
    # Apply in descending byte offset so earlier method-body offsets remain valid.
    for (length_at, code_at, old_code), new_code in sorted(
            replacements, key=lambda pair: pair[0][1], reverse=True):
        if old_code == new_code:
            continue
        old_length = patch_helpers.encode_u30(len(old_code))
        new_length = patch_helpers.encode_u30(len(new_code))
        if new_abc[length_at:length_at + len(old_length)] != old_length:
            raise ValueError("unexpected ABC method length")
        new_abc[code_at:code_at + len(old_code)] = new_code
        new_abc[length_at:length_at + len(old_length)] = new_length
    new_abc = bytes(new_abc)

    raw[abc_start:abc_end] = new_abc
    delta = len(new_abc) - len(abc)
    struct.pack_into("<I", raw, tag_length_at, tag_length + delta)
    struct.pack_into("<I", raw, 4, len(raw))
    if packed[:3] == b"CWS":
        output = bytes(raw[:8]) + zlib.compress(bytes(raw[8:]), level=9)
    else:
        output = bytes(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)
    return hashlib.md5(packed).hexdigest(), hashlib.md5(output).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: patch_equipment_rank_material.py input.swf output.swf")
    before, after = patch(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"ModuleCard: {before} -> {after}")
