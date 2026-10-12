"""Clamp the original selected-reward quantity to 999, preserving AVM2 classes."""
from pathlib import Path
import hashlib
import importlib.util
import json
import struct
import zlib

SERVER = Path(r'D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC')
WEBROOT = SERVER.parent / 'webroot'
BACKUP = Path(r'D:\pvzol本地最新最新ol\修复备份\20261009-本轮问题修复\webroot')
HERE = SERVER / 'client_patches' / 'rewards-rules-20261009'
spec = importlib.util.spec_from_file_location('bag_abc_tools', SERVER / 'client_patches/endless-verifyerror-20261009/abc_tools.py')
abc_tools = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = abc_tools
spec.loader.exec_module(abc_tools)
u30 = abc_tools.encode_u30


def patch_module(source):
    packed = source.read_bytes()
    raw = packed[:8] + zlib.decompress(packed[8:]) if packed[:3] == b'CWS' else packed
    assert len(raw) == int.from_bytes(raw[4:8], 'little')
    start, end, tag_length_at, header_size, tag_len = abc_tools.find_abc(raw)
    assert header_size == 6
    original = raw[start:end]
    before = abc_tools.parse_abc(original)
    def mn(name, package=''):
        indices = []
        for index, entry in enumerate(before.multinames):
            if not entry or entry[0] not in (7, 13):
                continue
            ns, text = entry[1:]
            if before.strings[text] == name and before.strings[before.namespaces[ns][1]] == package:
                indices.append(index)
        assert len(indices) == 1, (name, package, indices)
        return indices[0]
    target = ('com.qq.modules.bag.view.components.BagChooseRewardWindowMediator', 'refresh')
    mid = before.class_methods[target]
    _length_at, code_at, size = before.method_bodies[mid]
    header, max_stack, locals_count, scope, max_scope = before.method_headers[mid]
    code = original[code_at:code_at + size]
    old = b'\xd1\x61' + u30(mn('maxValue'))
    new = b'\x60' + u30(mn('Math')) + b'\xd1\x25' + u30(999) + b'\x46' + u30(mn('min')) + u30(2) + b'\x61' + u30(mn('maxValue'))
    assert code.count(old) == 1
    offset = code.index(old)
    assert offset == 41, 'Original refresh preamble changed; inspect before patching.'
    # No branch or exception precedes/crosses this straight-line preamble.
    # Every original branch source/target follows the splice and moves equally.
    assert abc_tools.u30(original, code_at + size)[0] == 0
    code = code[:offset] + new + code[offset + len(old):]
    prefix = b''.join(u30(value) for value in (mid, max(4, max_stack), locals_count, scope, max_scope, len(code)))
    abc = original[:header] + prefix + code + original[code_at + size:]
    after = abc_tools.parse_abc(abc)
    assert before.strings == after.strings
    assert before.namespaces == after.namespaces and before.multinames == after.multinames
    assert before.class_methods == after.class_methods
    assert before.method_bodies.keys() == after.method_bodies.keys()
    for method, (_la, ca, length) in before.method_bodies.items():
        if method == mid:
            continue
        _ala, aca, alength = after.method_bodies[method]
        assert original[ca:ca + length] == abc[aca:aca + alength], method
        assert before.method_headers[method][1:] == after.method_headers[method][1:], method
    result = bytearray(raw[:start] + abc + raw[end:])
    struct.pack_into('<I', result, tag_length_at, tag_len + len(abc) - len(original))
    struct.pack_into('<I', result, 4, len(result))
    result[:3] = b'CWS'
    output = bytes(result[:8]) + zlib.compress(bytes(result[8:]), 9)
    return output, dict(method=target, method_id=mid, body_count=len(before.method_bodies),
                        unchanged_bodies=len(before.method_bodies) - 1,
                        original_sha256=hashlib.sha256(packed).hexdigest(),
                        patched_sha256=hashlib.sha256(output).hexdigest())


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    BACKUP.mkdir(parents=True, exist_ok=True)
    source = WEBROOT / 'ModuleBag.swf'
    output, manifest = patch_module(source)
    targets = sorted(WEBROOT.glob('ModuleBag*.swf'))
    manifest['files'] = [path.name for path in targets]
    for path in targets:
        dest = BACKUP / path.name
        if not dest.exists():
            dest.write_bytes(path.read_bytes())
        path.write_bytes(output)
    (HERE / 'bag_batch_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
