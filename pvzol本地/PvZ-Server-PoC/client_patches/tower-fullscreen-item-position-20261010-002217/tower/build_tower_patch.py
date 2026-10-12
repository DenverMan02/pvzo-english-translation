"""Build isolated SWF candidates by editing selected AVM2 method bodies only."""
from pathlib import Path
import hashlib
import importlib.util
import json
import struct
import sys
import zlib

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'validation'))
from avm2_validate import instructions
PROJECT = Path('D:/pvzol本地最新最新ol/pvzol本地')
TOOLS = HERE.parent / 'validation/abc_tools.py'
spec = importlib.util.spec_from_file_location('tower_abc_tools', TOOLS)
abc_tools = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = abc_tools
spec.loader.exec_module(abc_tools)
parse_abc, find_abc, u30, encode_u30 = (getattr(abc_tools, n) for n in ('parse_abc', 'find_abc', 'u30', 'encode_u30'))

def qname(info, idx):
    item = info.multinames[idx]
    if item[0] in (7, 13):
        _, ns, si = item
        return info.strings[info.namespaces[ns][1]], info.strings[si]
    if item[0] in (9, 14):
        return None, info.strings[item[1]]
    return None, None

def mn(info, name, package=None):
    matches = [i for i, item in enumerate(info.multinames) if item and qname(info, i)[1] == name and (package is None or qname(info, i)[0] == package)]
    assert len(matches) == 1, (name, package, matches)
    return matches[0]

def unpack(path):
    packed = path.read_bytes()
    assert packed[:3] in (b'CWS', b'FWS')
    raw = packed[:8] + zlib.decompress(packed[8:]) if packed[:3] == b'CWS' else packed
    assert len(raw) == int.from_bytes(raw[4:8], 'little')
    return packed, raw

def context(path):
    packed, raw = unpack(path)
    start, end, length_at, hs, tag_len = find_abc(raw)
    assert hs == 6
    abc = raw[start:end]
    return dict(path=path, packed=packed, raw=raw, start=start, end=end, length_at=length_at, tag_len=tag_len, abc=abc, info=parse_abc(abc))

class Assembler:
    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.fixups = []
    def op(self, opcode, *args):
        self.code.append(opcode)
        for a in args:
            self.code.extend(encode_u30(a))
        return self
    def label(self, name):
        self.labels[name] = len(self.code)
        return self
    def branch(self, opcode, name):
        self.code.append(opcode)
        at = len(self.code)
        self.code.extend(b'\x00' * 3)
        self.fixups.append((at, name))
        return self
    def finish(self):
        for at, name in self.fixups:
            delta = self.labels[name] - (at + 3)
            assert -(1 << 23) <= delta < (1 << 23)
            self.code[at:at+3] = (delta & 0xffffff).to_bytes(3, 'little')
        return bytes(self.code)

def body(ctx, cls, method):
    info = ctx['info']
    mid = info.class_methods[(cls, method)]
    _, p, n = info.method_bodies[mid]
    return mid, ctx['abc'][p:p+n]

def nop_once(code, needle):
    assert code.count(needle) == 1, (needle.hex(), code.count(needle))
    p = code.index(needle)
    return code[:p] + b'\x02' * len(needle) + code[p+len(needle):], dict(start=p, length=len(needle), before_hex=needle.hex())

def insert_after_dispose(code, info):
    original = list(instructions(code).values())
    inserts = {}
    fields = []
    for index, ins in enumerate(original):
        if ins.opcode == 0x4f and qname(info, ins.args[0])[1] == 'dispose':
            before = original[index-1]
            assert before.opcode == 0x66
            field = before.args[0]
            name = qname(info, field)[1]
            assert name in ('mVertexBuffer', 'mIndexBuffer')
            fields.append(name)
            inserts[ins.next_offset] = bytes([0xd0, 0x20, 0x61]) + encode_u30(field)
    assert fields == ['mVertexBuffer', 'mIndexBuffer']
    new_at = lambda offset: offset + sum(len(v) for k, v in inserts.items() if k <= offset)
    result = bytearray()
    for ins in original:
        if ins.offset in inserts:
            result.extend(inserts[ins.offset])
        assert len(result) == new_at(ins.offset)
        piece = bytearray(code[ins.offset:ins.next_offset])
        if ins.targets:
            assert len(ins.targets) == 1 and ins.opcode != 0x1b
            delta = new_at(ins.targets[0]) - (len(result) + len(piece))
            piece[1:4] = (delta & 0xffffff).to_bytes(3, 'little')
        result.extend(piece)
    instructions(bytes(result))
    return bytes(result), dict(cleared_disposed_references=fields, insertion_offsets=list(inserts))

def make_game():
    ctx = context(PROJECT / 'webroot/Game_p.swf')
    info = ctx['info']
    patches = {}
    cls = 'starling.core.Starling'
    mid, code = body(ctx, cls, 'initialize')
    # Keep the branch layout and verifier metadata intact. Rendering resumes
    # through the already registered ENTER_FRAME after native context listeners.
    render = mn(info, 'render', '')
    needle = bytes([0xd0, 0x4f]) + encode_u30(render) + b'\x00'
    patched, edit = nop_once(code, needle)
    patches[(cls, 'initialize')] = (patched, None, edit)

    cls = 'starling.display.QuadBatch'
    mid, code = body(ctx, cls, 'createBuffers')
    patched, edit = insert_after_dispose(code, info)
    patches[(cls, 'createBuffers')] = (patched, None, edit)
    return write_candidate(ctx, patches, HERE / 'Game_p.tower.swf')

def make_td():
    ctx = context(PROJECT / 'webroot/ModuleTD_p.swf')
    info = ctx['info']
    cls = 'com.qq.modules.td.view.game.TDBGViewMediator'
    mid, code = body(ctx, cls, 'checkSize')
    needle = (bytes([0x60]) + encode_u30(mn(info, 'GameGloble', 'com.qq'))
              + bytes([0x66]) + encode_u30(mn(info, 'starling', ''))
              + bytes([0x5d]) + encode_u30(mn(info, 'Rectangle', 'flash.geom'))
              + bytes([0x24, 0, 0x2a, 0xd2, 0xd3, 0x4a]) + encode_u30(mn(info, 'Rectangle', 'flash.geom')) + bytes([4, 0x61]) + encode_u30(mn(info, 'viewPort')))
    patched, edit = nop_once(code, needle)
    return write_candidate(ctx, {(cls, 'checkSize'): (patched, None, edit)}, HERE / 'ModuleTD_p.tower.swf')

def write_candidate(ctx, patches, dest):
    before = ctx['info']
    abc = ctx['abc']
    edits = []
    for (cls, name), (code, limits, detail) in patches.items():
        mid = before.class_methods[(cls, name)]
        header, max_stack, locals_count, scope, max_scope = before.method_headers[mid]
        _, cp, size = before.method_bodies[mid]
        if limits is not None:
            max_stack, locals_count = limits
        prefix = b''.join(encode_u30(x) for x in (mid, max_stack, locals_count, scope, max_scope, len(code)))
        ec, _ = u30(abc, cp+size)
        assert ec == 0, (cls, name, 'exception table needs relocation')
        edits.append((header, cp+size, prefix+code, mid, cls, name, detail))
    for a, b, replacement, *_ in sorted(edits, reverse=True):
        abc = abc[:a] + replacement + abc[b:]
    after = parse_abc(abc)
    changed = {e[3] for e in edits}
    assert before.class_methods == after.class_methods
    assert before.strings == after.strings
    assert before.namespaces == after.namespaces
    assert before.multinames == after.multinames
    assert before.method_bodies.keys() == after.method_bodies.keys()
    # All ABC data before the first method body, including inheritance,
    # scripts, signatures, metadata and constant pools, must be byte identical.
    first = min(h[0] for h in before.method_headers.values())
    assert ctx['abc'][:first] == abc[:first]
    for mid, (_, p, size) in before.method_bodies.items():
        _, ap, an = after.method_bodies[mid]
        if mid not in changed:
            assert ctx['abc'][p:p+size] == abc[ap:ap+an], mid
            assert before.method_headers[mid][1:] == after.method_headers[mid][1:], mid
    raw = bytearray(ctx['raw'][:ctx['start']] + abc + ctx['raw'][ctx['end']:])
    struct.pack_into('<I', raw, ctx['length_at'], ctx['tag_len'] + len(abc) - len(ctx['abc']))
    struct.pack_into('<I', raw, 4, len(raw))
    raw[:3] = b'CWS'
    output = bytes(raw[:8]) + zlib.compress(bytes(raw[8:]), 9)
    dest.write_bytes(output)
    manifest = dict(source=str(ctx['path']), output=str(dest), source_sha256=hashlib.sha256(ctx['packed']).hexdigest(), output_sha256=hashlib.sha256(output).hexdigest(), methods=[dict(classname=e[4], name=e[5], method_id=e[3], detail=e[6]) for e in edits], total_method_bodies=len(before.method_bodies), unchanged_method_bodies=len(before.method_bodies)-len(changed), constant_pools_and_class_structure_unchanged=True)
    dest.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest

if __name__ == '__main__':
    manifests = [make_game(), make_td()]
    install = []
    for item in manifests:
        candidate = Path(item['output'])
        packed = candidate.read_bytes()
        install.append(dict(file=Path(item['source']).name, input_sha256=item['source_sha256'], output_sha256=item['output_sha256'], output_md5=hashlib.md5(packed).hexdigest(), size=len(packed), candidate=str(candidate)))
    (HERE / 'install_manifest.json').write_text(json.dumps(install, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifests, ensure_ascii=False, indent=2))
