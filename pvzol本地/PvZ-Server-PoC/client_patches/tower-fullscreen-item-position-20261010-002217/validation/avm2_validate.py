"""Fail-closed AVM2 stack/branch checks and safe minimal body replacement.

Uses the game's existing abc_tools parser. This checks structural stack/scope
validity; it is not a substitute for Flash's type verifier or a live UI check.
Operand encodings cross-checked against Ruffle swf/src/avm2/{opcode,read}.rs.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib
from pathlib import Path
from abc_tools import encode_u30, find_abc, parse_abc, u30


ONE_U30 = {
    0x04, 0x05, 0x06, 0x08, 0x25, 0x2C, 0x2D, 0x2E, 0x2F, 0x31,
    0x40, 0x41, 0x42, 0x49, 0x53, 0x55, 0x56, 0x58, 0x59, 0x5A,
    0x5D, 0x5E, 0x5F, 0x60, 0x61, 0x62, 0x63, 0x66, 0x67, 0x68,
    0x6A, 0x6C, 0x6D, 0x6E, 0x6F, 0x80, 0x86, 0x92, 0x94, 0xB2,
    0xC2, 0xC3, 0xF0, 0xF1, 0xF2,
}
TWO_U30 = {0x32, 0x43, 0x44, 0x45, 0x46, 0x4A, 0x4C, 0x4E, 0x4F}
NO_ARGS = {
    0x01, 0x02, 0x03, 0x07, 0x09, 0x1C, 0x1D, 0x1E, 0x1F,
    0x20, 0x21, 0x23, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B, 0x30,
    0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E,
    0x47, 0x48, 0x50, 0x51, 0x52, 0x57, 0x64,
    0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78,
    0x81, 0x82, 0x83, 0x84, 0x85, 0x87, 0x88, 0x89,
    0x90, 0x91, 0x93, 0x95, 0x96, 0x97,
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9,
    0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB3, 0xB4,
    0xC0, 0xC1, 0xC4, 0xC5, 0xC6, 0xC7,
    0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xF3,
}
BRANCHES = set(range(0x0C, 0x1B))
PUSHES = {0x20, 0x21, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D,
          0x2E, 0x2F, 0x31, 0x40, 0x57, 0x5A, 0x60, 0x62, 0x64,
          0x65, 0x67, 0x6E, 0xD0, 0xD1, 0xD2, 0xD3}
UNARY = {0x35, 0x36, 0x37, 0x38, 0x39, 0x50, 0x51, 0x52, 0x58,
         0x6C, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77,
         0x78, 0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x88,
         0x89, 0x90, 0x91, 0x93, 0x95, 0x96, 0x97, 0xB2, 0xC0,
         0xC1, 0xC4}
BINARY = {0x1E, 0x1F, 0x23, 0x87, 0xA0, 0xA1, 0xA2, 0xA3,
          0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB, 0xAC,
          0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB3, 0xB4, 0xC5, 0xC6, 0xC7}
NO_EFFECT = {0x01, 0x02, 0x06, 0x08, 0x09, 0x10, 0x1D, 0x47,
             0x92, 0x94, 0xC2, 0xC3, 0xEF, 0xF0, 0xF1, 0xF2, 0xF3}


@dataclass(frozen=True)
class Instruction:
    offset: int
    opcode: int
    args: tuple[int, ...]
    next_offset: int
    targets: tuple[int, ...] = ()


def instructions(code: bytes) -> dict[int, Instruction]:
    out = {}
    p = 0
    while p < len(code):
        at = p
        op = code[p]
        p += 1
        args = ()
        targets = ()
        if op in ONE_U30:
            a, p = u30(code, p)
            args = (a,)
        elif op in TWO_U30:
            a, p = u30(code, p)
            b, p = u30(code, p)
            args = (a, b)
        elif op in (0x24, 0x65):
            if p >= len(code):
                raise AssertionError(f'Truncated byte operand at {at}')
            args = (int.from_bytes(code[p:p+1], 'little', signed=op == 0x24),)
            p += 1
        elif op in BRANCHES:
            if p + 3 > len(code):
                raise AssertionError(f'Truncated branch at {at}')
            delta = int.from_bytes(code[p:p+3], 'little', signed=True)
            p += 3
            targets = (p + delta,)
            args = (delta,)
        elif op == 0x1B:
            if p + 3 > len(code):
                raise AssertionError(f'Truncated switch at {at}')
            default = int.from_bytes(code[p:p+3], 'little', signed=True)
            p += 3
            count, p = u30(code, p)
            if p + (count + 1) * 3 > len(code):
                raise AssertionError(f'Truncated switch cases at {at}')
            deltas = [int.from_bytes(code[p+i*3:p+i*3+3], 'little', signed=True)
                      for i in range(count + 1)]
            p += (count + 1) * 3
            args = (default, count, *deltas)
            targets = (at + default, *(at + delta for delta in deltas))
        elif op == 0xEF:
            kind = code[p]
            name, p = u30(code, p + 1)
            register = code[p]
            extra, p = u30(code, p + 1)
            args = (kind, name, register, extra)
        elif op not in NO_ARGS:
            raise AssertionError(f'Unsupported opcode 0x{op:02x} at {at}')
        if p > len(code):
            raise AssertionError(f'Truncated instruction at {at}')
        out[at] = Instruction(at, op, args, p, targets)
    for ins in out.values():
        for target in ins.targets:
            if target not in out:
                raise AssertionError(f'Branch {ins.offset} targets non-instruction {target}')
    return out


def runtime_name_arity(info, index: int) -> int:
    assert 0 < index < len(info.multinames), f'Invalid multiname index {index}'
    kind = info.multinames[index][0]
    if kind in (0x0F, 0x10, 0x1B, 0x1C):
        return 1
    if kind in (0x11, 0x12):
        return 2
    return 0


def stack_effect(ins: Instruction, info) -> tuple[int, int]:
    op, args = ins.opcode, ins.args
    extra = 0
    if op in {0x04, 0x05, 0x45, 0x46, 0x4A, 0x4C, 0x4E, 0x4F,
              0x59, 0x5D, 0x5E, 0x5F, 0x60, 0x61, 0x66, 0x68, 0x6A}:
        extra = runtime_name_arity(info, args[0]) if info else 0
    if op in PUSHES:
        return extra, 1
    if op in (0x5D, 0x5E, 0x5F):
        return extra, 1
    if op in UNARY:
        return 1, 1
    if op in BINARY:
        return 2, 1
    if op in (0x04, 0x59, 0x66, 0x6A):
        return 1 + extra, 1
    if op in (0x05, 0x61, 0x68):
        return 2 + extra, 0
    if op in (0x07, 0x1C, 0x29, 0x30, 0x63, 0x6F, 0xD4, 0xD5, 0xD6, 0xD7):
        return 1, 0
    if op == 0x2A:
        return 1, 2
    if op == 0x2B:
        return 2, 2
    if op == 0x32:
        return 0, 1
    if op in (0x11, 0x12, 0x1B, 0x03, 0x48):
        return 1, 0
    if op in BRANCHES - {0x10, 0x11, 0x12}:
        return 2, 0
    if op in (0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x6D):
        return 2, 0
    if op == 0x41:
        return args[0] + 2, 1
    if op == 0x42:
        return args[0] + 1, 1
    if op in (0x43, 0x44, 0x45, 0x46, 0x4A, 0x4C):
        return args[1] + 1 + extra, 1
    if op in (0x4E, 0x4F):
        return args[1] + 1 + extra, 0
    if op == 0x49:
        return args[0] + 1, 0
    if op == 0x53:
        return args[0] + 1, 1
    if op == 0x55:
        return args[0] * 2, 1
    if op == 0x56:
        return args[0], 1
    if op in NO_EFFECT:
        return 0, 0
    raise AssertionError(f'No stack rule for 0x{op:02x} at {ins.offset}')


def verify_method(code: bytes, header, info=None, exceptions=()) -> dict:
    """Validate control flow, stack height, locals and scope for one method."""
    _, max_stack, local_count, initial, max_scope = header
    assert 0 <= initial <= max_scope
    assert local_count >= 1
    ins = instructions(code)
    assert ins, 'Empty method body'
    for instruction in ins.values():
        op, args = instruction.opcode, instruction.args
        registers = []
        if op in (0x08, 0x62, 0x63, 0x92, 0x94, 0xC2, 0xC3):
            registers = [args[0]]
        elif 0xD0 <= op <= 0xD7:
            registers = [(op - 0xD0) % 4]
        elif op == 0x32:
            registers = list(args)
        for register in registers:
            assert register < local_count, f'Local {register} beyond local_count at {instruction.offset}'
    queue = [(0, 0, initial)]
    for start, end, target, typename, varname in exceptions:
        assert start in ins and (end in ins or end == len(code)) and start < end
        assert target in ins, f'Bad exception target {target}'
        queue.append((target, 1, initial))
    seen = {}
    highest = 0
    highest_scope = initial
    returns = 0
    while queue:
        pc, depth, scope = queue.pop()
        if pc in seen:
            assert seen[pc] == (depth, scope), f'Inconsistent merge at {pc}: {seen[pc]} vs {(depth, scope)}'
            continue
        assert pc in ins, f'Execution falls outside code at {pc}'
        seen[pc] = (depth, scope)
        i = ins[pc]
        op = i.opcode
        if op == 0x65:
            assert i.args[0] < scope, f'getscopeobject {i.args[0]} outside scope {scope} at {pc}'
        if op == 0x67:
            assert i.args[0] < initial, f'getouterscope {i.args[0]} outside initial scope {initial} at {pc}'
        take, give = stack_effect(i, info)
        assert depth >= take, f'Stack underflow at {pc}: opcode {op:02x}, depth {depth}, requires {take}'
        depth += give - take
        highest = max(highest, depth)
        assert depth <= max_stack, f'max_stack {max_stack} too small for {depth} at {pc}'
        if op in (0x1C, 0x30):
            scope += 1
        if op == 0x1D:
            scope -= 1
        highest_scope = max(highest_scope, scope)
        assert initial <= scope <= max_scope, f'Scope {scope} outside {initial}..{max_scope} at {pc}'
        if op in (0x03, 0x47, 0x48):
            if op != 0x03:
                assert depth == 0, f'Extra operand stack items at return {pc}'
                returns += 1
            continue
        if op in (0x10, 0x1B):
            queue.extend((target, depth, scope) for target in i.targets)
        elif op in BRANCHES:
            queue.extend((target, depth, scope) for target in i.targets)
            queue.append((i.next_offset, depth, scope))
        else:
            queue.append((i.next_offset, depth, scope))
    return dict(max_stack_used=highest, max_scope_used=highest_scope,
                instruction_count=len(ins), reachable_count=len(seen), return_count=returns)


def skip_traits(data: bytes, pos: int) -> int:
    count, pos = u30(data, pos)
    for _ in range(count):
        _, pos = u30(data, pos)
        kind_attr = data[pos]
        pos += 1
        kind = kind_attr & 15
        if kind in (0, 6):
            _, pos = u30(data, pos)
            _, pos = u30(data, pos)
            value, pos = u30(data, pos)
            if value:
                pos += 1
        elif kind in (1, 2, 3, 4, 5):
            _, pos = u30(data, pos)
            _, pos = u30(data, pos)
        else:
            raise AssertionError(f'Unknown trait kind {kind}')
        if kind_attr & 0x40:
            count_metadata, pos = u30(data, pos)
            for _ in range(count_metadata):
                _, pos = u30(data, pos)
    return pos


def body_tail(abc: bytes, info, method_id: int):
    _, code_at, size = info.method_bodies[method_id]
    tail_at = code_at + size
    count, pos = u30(abc, tail_at)
    exceptions = []
    for _ in range(count):
        entry = []
        for _ in range(5):
            value, pos = u30(abc, pos)
            entry.append(value)
        exceptions.append(tuple(entry))
    traits_at = pos
    end = skip_traits(abc, traits_at)
    return exceptions, traits_at, end


@dataclass(frozen=True)
class BodyReplacement:
    code: bytes
    max_stack: int | None = None
    local_count: int | None = None
    init_scope: int | None = None
    max_scope: int | None = None
    exceptions: tuple | None = None


def replace_bodies(abc: bytes, replacements: dict[int, BodyReplacement]):
    """Replace only target bodies; preserve all classes, pools and other bodies.

    A changed-length body with old catch handlers requires explicit new handler
    offsets, so stale offsets cannot be copied silently.
    """
    before = parse_abc(abc)
    chunks = []
    reports = {}
    for mid, replacement in replacements.items():
        old_header = before.method_headers[mid]
        start, old_stack, old_locals, old_init, old_max_scope = old_header
        _, code_at, code_size = before.method_bodies[mid]
        old_exceptions, traits_at, end = body_tail(abc, before, mid)
        if replacement.exceptions is None:
            if old_exceptions and len(replacement.code) != code_size:
                raise AssertionError(f'Method {mid} has catch handlers; provide new exception offsets')
            exceptions = old_exceptions
        else:
            exceptions = replacement.exceptions
        values = (mid,
                  old_stack if replacement.max_stack is None else replacement.max_stack,
                  old_locals if replacement.local_count is None else replacement.local_count,
                  old_init if replacement.init_scope is None else replacement.init_scope,
                  old_max_scope if replacement.max_scope is None else replacement.max_scope)
        report = verify_method(replacement.code, (0, *values[1:]), before, exceptions)
        encoded = b''.join(encode_u30(v) for v in (*values, len(replacement.code)))
        encoded += replacement.code + encode_u30(len(exceptions))
        for exception in exceptions:
            assert len(exception) == 5
            encoded += b''.join(encode_u30(v) for v in exception)
        encoded += abc[traits_at:end]
        chunks.append((start, end, encoded))
        reports[mid] = report
    changed = abc
    for start, end, encoded in sorted(chunks, reverse=True):
        changed = changed[:start] + encoded + changed[end:]
    after = parse_abc(changed)
    assert before.class_methods == after.class_methods
    assert before.namespaces == after.namespaces
    assert before.multinames == after.multinames
    assert before.strings == after.strings
    assert before.method_bodies.keys() == after.method_bodies.keys()
    for mid in before.method_bodies:
        if mid in replacements:
            continue
        a = before.method_headers[mid][0]
        _, _, b = body_tail(abc, before, mid)
        ca = after.method_headers[mid][0]
        _, _, cb = body_tail(changed, after, mid)
        assert abc[a:b] == changed[ca:cb], f'Untargeted body {mid} changed'
    return changed, reports


def unpack_swf(path: str | Path) -> bytes:
    packed = Path(path).read_bytes()
    assert packed[:3] in (b'CWS', b'FWS')
    raw = packed[:8] + zlib.decompress(packed[8:]) if packed[:3] == b'CWS' else packed
    assert len(raw) == int.from_bytes(raw[4:8], 'little')
    return raw


def patch_swf_bytes(raw: bytes, replacements: dict[int, BodyReplacement]):
    a, b, length_at, header_size, tag_length = find_abc(raw)
    assert header_size == 6, 'DoABC short tag needs explicit rebuilding'
    changed, report = replace_bodies(raw[a:b], replacements)
    result = bytearray(raw[:a] + changed + raw[b:])
    struct.pack_into('<I', result, length_at, tag_length + len(changed) - (b-a))
    struct.pack_into('<I', result, 4, len(result))
    result[:3] = b'CWS'
    packed = bytes(result[:8]) + zlib.compress(bytes(result[8:]), 9)
    return packed, report
