#!/usr/bin/env python3
"""Patch CardBeanItem's flash overlay hit testing without recompiling the ABC."""

from __future__ import annotations

import argparse
import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


def u30(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(5):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid U30")


def s32(data: bytes, offset: int) -> int:
    while data[offset] & 0x80:
        offset += 1
    return offset + 1


def skip_u30(data: bytes, offset: int) -> int:
    return u30(data, offset)[1]


def encode_u30(value: int) -> bytes:
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


@dataclass
class ABCInfo:
    strings: list[str]
    namespaces: list[tuple[int, int]]
    multinames: list[tuple]
    class_methods: dict[tuple[str, str], int]
    method_bodies: dict[int, tuple[int, int, int]]  # method id -> (length offset, code offset, code length)


def parse_abc(data: bytes) -> ABCInfo:
    pos = 4  # minor_version, major_version

    count, pos = u30(data, pos)
    for _ in range(max(0, count - 1)):
        pos = s32(data, pos)

    count, pos = u30(data, pos)
    for _ in range(max(0, count - 1)):
        pos = s32(data, pos)

    count, pos = u30(data, pos)
    pos += max(0, count - 1) * 8

    count, pos = u30(data, pos)
    strings = [""]
    for _ in range(max(0, count - 1)):
        size, pos = u30(data, pos)
        strings.append(data[pos : pos + size].decode("utf-8", "replace"))
        pos += size

    count, pos = u30(data, pos)
    namespaces = [(0, 0)]
    for _ in range(max(0, count - 1)):
        kind = data[pos]
        name, pos = u30(data, pos + 1)
        namespaces.append((kind, name))

    count, pos = u30(data, pos)
    for _ in range(max(0, count - 1)):
        n, pos = u30(data, pos)
        for _ in range(n):
            _, pos = u30(data, pos)

    count, pos = u30(data, pos)
    multinames = [None]
    for _ in range(max(0, count - 1)):
        kind = data[pos]
        pos += 1
        if kind in (0x07, 0x0D):  # QName / QNameA
            ns, pos = u30(data, pos)
            name, pos = u30(data, pos)
            multinames.append((kind, ns, name))
        elif kind in (0x0F, 0x10):  # RTQName / RTQNameA
            _, pos = u30(data, pos)
            multinames.append((kind,))
        elif kind in (0x11, 0x12):  # RTQNameL / RTQNameLA
            multinames.append((kind,))
        elif kind in (0x1B, 0x1C):  # MultinameL / MultinameLA
            _, pos = u30(data, pos)
            multinames.append((kind,))
        elif kind in (0x09, 0x0E):  # Multiname / MultinameA
            name, pos = u30(data, pos)
            ns_set, pos = u30(data, pos)
            multinames.append((kind, name, ns_set))
        elif kind == 0x1D:  # TypeName
            base, pos = u30(data, pos)
            n, pos = u30(data, pos)
            params = []
            for _ in range(n):
                item, pos = u30(data, pos)
                params.append(item)
            multinames.append((kind, base, tuple(params)))
        else:
            raise ValueError(f"unsupported multiname kind 0x{kind:02x}")

    method_count, pos = u30(data, pos)
    for _ in range(method_count):
        param_count, pos = u30(data, pos)
        _, pos = u30(data, pos)  # return type
        for _ in range(param_count):
            _, pos = u30(data, pos)
        name_index, pos = u30(data, pos)
        flags = data[pos]
        pos += 1
        if flags & 0x08:
            optional_count, pos = u30(data, pos)
            for _ in range(optional_count):
                _, pos = u30(data, pos)
                pos += 1
        if flags & 0x80:
            for _ in range(param_count):
                _, pos = u30(data, pos)

    metadata_count, pos = u30(data, pos)
    for _ in range(metadata_count):
        _, pos = u30(data, pos)  # name
        item_count, pos = u30(data, pos)
        for _ in range(item_count * 2):
            _, pos = u30(data, pos)

    def qname_parts(index: int) -> tuple[str, str] | None:
        if index <= 0 or index >= len(multinames):
            return None
        item = multinames[index]
        if not item or item[0] not in (0x07, 0x0D):
            return None
        _, ns_index, name_index = item
        if ns_index >= len(namespaces) or name_index >= len(strings):
            return None
        ns_kind, ns_name_index = namespaces[ns_index]
        if ns_name_index >= len(strings):
            return None
        return strings[ns_name_index], strings[name_index]

    def skip_traits(cursor: int, section: str) -> tuple[list[tuple[str, int]], int]:
        trait_count, cursor = u30(data, cursor)
        methods: list[tuple[str, int]] = []
        for _ in range(trait_count):
            name_index, cursor = u30(data, cursor)
            kind_attr = data[cursor]
            cursor += 1
            kind = kind_attr & 0x0F
            if kind in (0, 6):
                _, cursor = u30(data, cursor)  # slot id
                _, cursor = u30(data, cursor)  # type name
                value_index, cursor = u30(data, cursor)
                if value_index:
                    cursor += 1
            elif kind in (1, 2, 3):
                _, cursor = u30(data, cursor)  # disp id
                method_index, cursor = u30(data, cursor)
                parts = qname_parts(name_index)
                methods.append((parts[1] if parts else "", method_index))
            elif kind in (4, 5):
                _, cursor = u30(data, cursor)
                _, cursor = u30(data, cursor)
            else:
                raise ValueError(f"unsupported trait kind {kind} in {section} at 0x{cursor:x}: {data[cursor - 8:cursor + 8].hex()}")
            if kind_attr & 0x40:
                metadata_items, cursor = u30(data, cursor)
                for _ in range(metadata_items):
                    _, cursor = u30(data, cursor)
        return methods, cursor

    instance_count, pos = u30(data, pos)
    class_methods: dict[tuple[str, str], int] = {}
    for instance_index in range(instance_count):
        class_index, pos = u30(data, pos)
        _, pos = u30(data, pos)  # superclass
        flags = data[pos]
        pos += 1
        if flags & 0x08:
            _, pos = u30(data, pos)
        interface_count, pos = u30(data, pos)
        for _ in range(interface_count):
            _, pos = u30(data, pos)
        _, pos = u30(data, pos)  # instance initializer
        traits, pos = skip_traits(pos, f"instance {instance_index}")
        parts = qname_parts(class_index)
        if parts:
            package_name, class_name = parts
            for method_name, method_index in traits:
                class_methods[(f"{package_name}.{class_name}", method_name)] = method_index

    for class_index in range(instance_count):
        _, pos = u30(data, pos)  # class initializer
        _, pos = skip_traits(pos, f"class {class_index}")

    script_count, pos = u30(data, pos)
    for script_index in range(script_count):
        _, pos = u30(data, pos)
        _, pos = skip_traits(pos, f"script {script_index}")

    body_count, pos = u30(data, pos)
    method_bodies = {}
    for _ in range(body_count):
        method_index, pos = u30(data, pos)
        _, pos = u30(data, pos)  # max stack
        _, pos = u30(data, pos)  # local count
        _, pos = u30(data, pos)  # init scope depth
        _, pos = u30(data, pos)  # max scope depth
        code_length_offset = pos
        code_length, pos = u30(data, pos)
        code_offset = pos
        pos += code_length
        method_bodies[method_index] = (code_length_offset, code_offset, code_length)
        exception_count, pos = u30(data, pos)
        for _ in range(exception_count):
            for _ in range(5):
                _, pos = u30(data, pos)
        _, pos = skip_traits(pos, f"method body {method_index}")

    return ABCInfo(strings, namespaces, multinames, class_methods, method_bodies)


def find_abc(raw: bytes) -> tuple[int, int, int, int, int]:
    nbits = raw[8] >> 3
    rect_length = (5 + 4 * nbits + 7) // 8
    pos = 8 + rect_length + 4  # RECT, frame rate, frame count
    while pos < len(raw):
        header = struct.unpack_from("<H", raw, pos)[0]
        tag_code = header >> 6
        tag_length = header & 0x3F
        header_length = 2
        if tag_length == 0x3F:
            tag_length = struct.unpack_from("<I", raw, pos + 2)[0]
            header_length = 6
        payload = pos + header_length
        end = payload + tag_length
        if tag_code == 82:  # DoABC2
            name_start = payload + 4
            name_end = raw.index(0, name_start, end)
            return name_end + 1, end, pos + 2, header_length, tag_length
        if tag_code == 0:
            break
        pos = end
    raise ValueError("no DoABC2 tag found")


def patch_file(source: Path, destination: Path) -> tuple[str, str]:
    packed = source.read_bytes()
    if packed[:3] == b"CWS":
        body = zlib.decompress(packed[8:])
        raw = bytearray(packed[:8] + body)
    elif packed[:3] == b"FWS":
        raw = bytearray(packed)
    else:
        raise ValueError("expected an unencrypted FWS/CWS SWF")

    abc_start, abc_end, tag_length_offset, tag_header_length, tag_length = find_abc(raw)
    if tag_header_length != 6:
        raise ValueError("expected a long-form DoABC tag; refusing an unsafe header rewrite")
    abc = bytes(raw[abc_start:abc_end])
    info = parse_abc(abc)
    target = ("com.qq.modules.card.view.bean.CardBeanItem", "InitializeComponent")
    method_index = info.class_methods.get(target)
    if method_index is None:
        raise ValueError(f"target method not found: {target}")
    body_info = info.method_bodies.get(method_index)
    if body_info is None:
        raise ValueError("target method body not found")
    code_length_offset, code_offset, code_length = body_info
    code = bytearray(abc[code_offset : code_offset + code_length])

    def qname_index(name: str) -> int:
        matches = []
        for index in range(1, len(info.multinames)):
            item = info.multinames[index]
            if not item or item[0] not in (0x07, 0x0D):
                continue
            parts = None
            ns_index, name_index = item[1], item[2]
            if ns_index < len(info.namespaces) and name_index < len(info.strings):
                ns_kind, ns_name_index = info.namespaces[ns_index]
                if ns_name_index < len(info.strings):
                    parts = (ns_kind, info.strings[ns_name_index], info.strings[name_index])
            if parts and parts[0] == 0x16 and parts[1] == "" and parts[2] == name:
                matches.append(index)
        if not matches:
            raise ValueError(f"QName not found: {name}")
        return matches[0]

    mouse_index = qname_index("mouseEnabled")
    mouse_children_index = qname_index("mouseChildren")
    flash_names = []
    for index, item in enumerate(info.multinames):
        if item and item[0] in (0x07, 0x0D):
            ns_index, name_index = item[1], item[2]
            if ns_index < len(info.namespaces) and name_index < len(info.strings):
                if info.strings[name_index] == "flashMc":
                    flash_names.append(index)

    stop_index = qname_index("stop")
    old_call = b"\x4f" + encode_u30(stop_index) + b"\x00"  # callpropvoid stop, 0
    # The receiver is already on the stack. dup preserves it for the second
    # assignment, so this has the same net stack effect as callpropvoid stop.
    replacement = (
        b"\x2a\x27\x61" + encode_u30(mouse_index)
        + b"\x27\x61" + encode_u30(mouse_children_index)
    )

    matches: list[int] = []
    start_at = 0
    while True:
        at = code.find(old_call, start_at)
        if at < 0:
            break
        start_at = at + 1
        for flash_index in flash_names:
            prefix = b"\xd0\x66" + encode_u30(flash_index)  # getlocal0; getproperty flashMc
            if at >= len(prefix) and code[at - len(prefix) : at] == prefix:
                matches.append(at)
                break
    if len(matches) != 1:
        raise ValueError(f"expected one flashMc.stop call in target method, found {len(matches)}")

    call_at = matches[0]
    absolute_code_at = abc_start + code_offset
    raw[absolute_code_at + call_at : absolute_code_at + call_at + len(old_call)] = replacement
    new_code_length = code_length + len(replacement) - len(old_call)
    absolute_length_at = abc_start + code_length_offset
    old_length_encoding = encode_u30(code_length)
    new_length_encoding = encode_u30(new_code_length)
    raw[absolute_length_at : absolute_length_at + len(old_length_encoding)] = new_length_encoding
    new_tag_length = tag_length + len(replacement) - len(old_call) + len(new_length_encoding) - len(old_length_encoding)
    struct.pack_into("<I", raw, tag_length_offset, new_tag_length)
    struct.pack_into("<I", raw, 4, len(raw))

    if packed[:3] == b"CWS":
        output = bytes(raw[:8]) + zlib.compress(bytes(raw[8:]), level=9)
        output = output[:4] + struct.pack("<I", len(raw)) + output[8:]
    else:
        output = bytes(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)
    return hashlib.md5(packed).hexdigest(), hashlib.md5(output).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    old_md5, new_md5 = patch_file(args.input, args.output)
    print(f"Patched {args.output}")
    print(f"Input MD5:  {old_md5}")
    print(f"Output MD5: {new_md5}")


if __name__ == "__main__":
    main()
