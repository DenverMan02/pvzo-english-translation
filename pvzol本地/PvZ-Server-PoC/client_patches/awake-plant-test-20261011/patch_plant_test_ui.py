"""Use server encounter power in the plant-test preview.

Only two AVM2 method bodies and one uint slot change. The wire's decorative
showCard.exp carries total encounter power; it is never applied as card EXP.
"""
from pathlib import Path
import hashlib
import json
import struct
import zlib
from abc_tools import parse_abc, find_abc, encode_u30, u30

VO = 'com.qq.modules.plantTest.model.vo.PlantTestChapterVO'
VIEW = 'com.qq.modules.plantTest.view.components.PlantTestViewMediator'


def unpack(packed):
    assert packed[:3] in (b'CWS', b'FWS')
    raw = packed[:8] + zlib.decompress(packed[8:]) if packed[:3] == b'CWS' else packed
    assert len(raw) == int.from_bytes(raw[4:8], 'little')
    return raw


def patch_plant_test_ui(packed):
    raw = unpack(packed)
    begin, end, taglen_at, header_size, taglen = find_abc(raw)
    assert header_size == 6
    source_abc = raw[begin:end]
    source_info = parse_abc(source_abc)
    # exp is an existing external CardBaseInfo uint getter, but this module
    # never referenced it. Append its QName without renumbering any symbols.
    pool = source_info.pool_ranges
    public_ns = next(i for i, (kind, name) in enumerate(source_info.namespaces)
                     if kind == 0x16 and source_info.strings[name] == '')
    assert 'exp' not in source_info.strings
    exp_string = len(source_info.strings)
    mn_start = pool['multinames_count_at']
    mn_entries = pool['multinames_entries_start']
    mn_end = pool['multinames_end']
    original = (source_abc[:mn_start] + encode_u30(len(source_info.multinames) + 1)
                + source_abc[mn_entries:mn_end]
                + b'\x07' + encode_u30(public_ns) + encode_u30(exp_string) + source_abc[mn_end:])
    # Locate string count immediately before its first entry.
    string_count = encode_u30(len(source_info.strings))
    string_start = pool['string_entries_start'] - len(string_count)
    assert original[string_start:pool['string_entries_start']] == string_count
    original = (original[:string_start] + encode_u30(len(source_info.strings) + 1)
                + original[pool['string_entries_start']:pool['strings_end']]
                + b'\x03exp' + original[pool['strings_end']:])
    before = parse_abc(original)

    def mn(name, namespace=None):
        hits = []
        for i, entry in enumerate(before.multinames):
            if not entry or entry[0] not in (7, 13):
                continue
            ns, text = entry[1:]
            if before.strings[text] == name and (namespace is None or before.strings[before.namespaces[ns][1]] == namespace):
                hits.append(i)
        assert len(hits) == 1, (name, namespace, hits)
        return hits[0]

    def code_for(target):
        mid = before.class_methods[target]
        length_at, start, size = before.method_bodies[mid]
        return mid, length_at, start, original[start:start + size]

    changes = []
    # The VO is sealed. Declare a typed slot instead of attaching a dynamic
    # property or assigning the template's read-only totalPower getter.
    trait_start, trait_end = before.instance_traits[VO]
    count, entries = u30(original, trait_start)
    trait = encode_u30(mn('totalPower', '')) + b'\x00\x00' + encode_u30(mn('uint', '')) + b'\x00'
    trait_replace = encode_u30(count + 1) + original[entries:trait_end] + trait

    mid, length_at, start, code = code_for((VO, 'update'))
    assert code[-1:] == b'\x47'
    write_power = b'\xd0\xd1\x66' + encode_u30(mn('exp', '')) + b'\x68' + encode_u30(mn('totalPower', ''))
    changes.append((length_at, start, len(code), code[:-1] + write_power + b'\x47', mid))

    mid, length_at, start, code = code_for((VIEW, 'updateChapter'))
    old_power = b'\x62\x04\x66' + encode_u30(mn('totalPower', ''))
    assert code.count(old_power) == 1
    # ChapterVO contains the power for the exact persisted enemy snapshot used
    # by battle. Avoid stale local recommendations when a player team changes.
    new_power = b'\xd2\x02\x66' + encode_u30(mn('totalPower', ''))
    assert len(new_power) == len(old_power)
    changes.append((length_at, start, len(code), code.replace(old_power, new_power), mid))

    edits = [(trait_start, trait_end, trait_replace)]
    for length_at, start, size, code, mid in changes:
        exceptions, _ = u30(original, start + size)
        assert exceptions == 0
        edits.append((length_at, start + size, encode_u30(len(code)) + code))
    abc = original
    for left, right, value in sorted(edits, reverse=True):
        abc = abc[:left] + value + abc[right:]
    after = parse_abc(abc)
    assert before.strings == after.strings
    assert before.namespaces == after.namespaces
    assert before.multinames == after.multinames
    assert before.class_methods == after.class_methods
    assert before.method_bodies.keys() == after.method_bodies.keys()
    changed_mids = {entry[4] for entry in changes}
    for method, (_, old_pos, old_size) in before.method_bodies.items():
        _, new_pos, new_size = after.method_bodies[method]
        assert before.method_headers[method][1:] == after.method_headers[method][1:]
        if method not in changed_mids:
            assert original[old_pos:old_pos + old_size] == abc[new_pos:new_pos + new_size], method
    for cls, (left, right) in before.instance_traits.items():
        nleft, nright = after.instance_traits[cls]
        if cls != VO:
            assert original[left:right] == abc[nleft:nright], cls
    nleft, nright = after.instance_traits[VO]
    assert abc[nleft:nright] == trait_replace
    # Appended update uses two stack entries, within its existing header.
    assert after.method_headers[before.class_methods[(VO, 'update')]][1] >= 2
    result = bytearray(raw[:begin] + abc + raw[end:])
    struct.pack_into('<I', result, taglen_at, taglen + len(abc) - len(source_abc))
    struct.pack_into('<I', result, 4, len(result))
    result[:3] = b'CWS'
    output = bytes(result[:8]) + zlib.compress(bytes(result[8:]), 9)
    manifest = dict(source_sha256=hashlib.sha256(packed).hexdigest(),
                    patched_sha256=hashlib.sha256(output).hexdigest(),
                    changed_methods=sorted(changed_mids), added_slot='PlantTestChapterVO.totalPower:uint',
                    body_count=len(before.method_bodies), unchanged_body_count=len(before.method_bodies)-2,
                    all_method_headers_preserved=True, all_other_class_traits_preserved=True)
    return output, manifest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    output, manifest = patch_plant_test_ui(args.source.read_bytes())
    args.destination.write_bytes(output)
    args.destination.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))
