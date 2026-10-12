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
    method_headers: dict
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
    string_entries_start = pos
    strings = [""]
    for _ in range(max(0, count - 1)):
        size, pos = u30(data, pos)
        strings.append(data[pos : pos + size].decode("utf-8", "replace"))
        pos += size

    strings_end = pos
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

    multinames_count_at = pos
    count, pos = u30(data, pos)
    multinames_entries_start = pos
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

    multinames_end = pos
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
    instance_traits = {}
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
        trait_start = pos
        traits, pos = skip_traits(pos, f"instance {instance_index}")
        parts = qname_parts(class_index)
        if parts:
            package_name, class_name = parts
            instance_traits[f"{package_name}.{class_name}"] = (trait_start, pos)
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
    method_headers = {}
    for _ in range(body_count):
        header_start = pos
        method_index, pos = u30(data, pos)
        max_stack, pos = u30(data, pos)
        local_count, pos = u30(data, pos)
        init_scope, pos = u30(data, pos)
        max_scope, pos = u30(data, pos)
        method_headers[method_index] = (header_start, max_stack, local_count, init_scope, max_scope)
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

    info = ABCInfo(method_headers, strings, namespaces, multinames, class_methods, method_bodies)
    info.instance_traits = instance_traits
    info.pool_ranges = dict(string_entries_start=string_entries_start, strings_end=strings_end,
                            multinames_count_at=multinames_count_at,
                            multinames_entries_start=multinames_entries_start, multinames_end=multinames_end)
    if pos != len(data):
        raise ValueError("ABC parsing did not consume the entire payload")
    return info


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

