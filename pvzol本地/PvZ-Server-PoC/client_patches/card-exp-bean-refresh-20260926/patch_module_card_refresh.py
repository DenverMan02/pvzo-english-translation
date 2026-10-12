#!/usr/bin/env python3
"""Keep experience-bean icons loaded across card-property refreshes.

Patch only the two CardBeanItem AVM2 method bodies in the original SWF.  FFDec
source re-import changes this module's class scope and causes VerifyError #1024.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "card-exp-bean-repeat-20260926"))
from patch_module_card_clickthrough import encode_u30, find_abc, parse_abc, u30  # noqa: E402


def patch_file(source: Path, destination: Path) -> tuple[str, str]:
    packed = source.read_bytes()
    if packed[:3] == b"CWS":
        raw = bytearray(packed[:8] + zlib.decompress(packed[8:]))
    elif packed[:3] == b"FWS":
        raw = bytearray(packed)
    else:
        raise ValueError("expected an FWS/CWS SWF")

    abc_start, abc_end, tag_length_offset, tag_header_length, tag_length = find_abc(raw)
    if tag_header_length != 6:
        raise ValueError("expected a long-form DoABC tag")
    abc = bytes(raw[abc_start:abc_end])
    info = parse_abc(abc)

    def qname(name: str, namespace: str = "") -> int:
        hits = []
        for index, entry in enumerate(info.multinames):
            if not entry or entry[0] not in (7, 13):
                continue
            ns_index, name_index = entry[1:3]
            ns_kind, ns_name_index = info.namespaces[ns_index]
            if ns_kind == 0x16 and info.strings[ns_name_index] == namespace and info.strings[name_index] == name:
                hits.append(index)
        if not hits:
            raise ValueError(f"missing QName {namespace}::{name}")
        return hits[0]

    class_name = "com.qq.modules.card.view.bean.CardBeanItem"
    icon_names = [i for i, e in enumerate(info.multinames) if e and e[0] in (7, 13)
                  and info.strings[e[2]] == "icon"]
    flash_names = [i for i, e in enumerate(info.multinames) if e and e[0] in (7, 13)
                   and info.strings[e[2]] == "flashMc"]
    changes = []

    # The initial load still calls setTemplate. Later CARD_PROP_UPDATED events
    # call setData on all three beans. Preserve the existing CardIcon if its
    # item ID did not change, while updating the tooltip and count each time.
    method = info.class_methods[(class_name, "setData")]
    length_at, code_at, code_length = info.method_bodies[method]
    code = abc[code_at:code_at + code_length]
    set_template = b"\x4f" + encode_u30(qname("setTemplate")) + b"\x01"
    matches = []
    for icon in icon_names:
        original = b"\xd0\x66" + encode_u30(icon) + b"\xd1" + set_template
        if code.count(original) == 1:
            matches.append(original)
    if len(matches) != 1:
        raise ValueError("expected one CardBeanItem icon.setTemplate call")
    original = matches[0]
    at = code.index(original)
    # Use the same private icon QName as the original call, not an unrelated
    # public QName named icon elsewhere in the ABC pool.
    icon_index, _ = u30(original, 2)
    guard = (b"\xd0\x66" + encode_u30(icon_index)
             + b"\x66" + encode_u30(qname("data"))
             + b"\xd1\x66" + encode_u30(qname("id"))
             + b"\xab\x11" + struct.pack("<i", len(original))[:3])
    changes.append((length_at, code_at, code_length, at, original, guard + original))

    # Preserve the original flashMc.stop() and additionally disable the flash
    # overlay's mouse hit testing. The previous patch accidentally removed stop.
    method = info.class_methods[(class_name, "InitializeComponent")]
    length_at, code_at, code_length = info.method_bodies[method]
    code = abc[code_at:code_at + code_length]
    stop_call = b"\x4f" + encode_u30(qname("stop")) + b"\x00"
    matches = []
    for flash in flash_names:
        prefix = b"\xd0\x66" + encode_u30(flash)
        if code.count(prefix + stop_call) == 1:
            matches.append(prefix + stop_call)
    if len(matches) != 1:
        raise ValueError("expected one flashMc.stop call in the original SWF")
    original = matches[0]
    at = code.index(original)
    replacement = (original[:-len(stop_call)]
                   + b"\x2a\x27\x61" + encode_u30(qname("mouseEnabled"))
                   + b"\x2a\x27\x61" + encode_u30(qname("mouseChildren"))
                   + stop_call)
    changes.append((length_at, code_at, code_length, at, original, replacement))

    total_delta = 0
    for length_at, code_at, code_length, at, original, replacement in sorted(changes, reverse=True):
        absolute = abc_start + code_at + at
        if raw[absolute:absolute + len(original)] != original:
            raise ValueError("method body changed during patch")
        raw[absolute:absolute + len(original)] = replacement
        old_length = encode_u30(code_length)
        new_length = encode_u30(code_length + len(replacement) - len(original))
        raw[abc_start + length_at:abc_start + length_at + len(old_length)] = new_length
        total_delta += len(replacement) - len(original) + len(new_length) - len(old_length)

    struct.pack_into("<I", raw, tag_length_offset, tag_length + total_delta)
    struct.pack_into("<I", raw, 4, len(raw))
    if packed[:3] == b"CWS":
        output = bytes(raw[:8]) + zlib.compress(bytes(raw[8:]), level=9)
        output = output[:4] + struct.pack("<I", len(raw)) + output[8:]
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
    before, after = patch_file(args.source, args.destination)
    print(f"{args.destination}: {before} -> {after}")
