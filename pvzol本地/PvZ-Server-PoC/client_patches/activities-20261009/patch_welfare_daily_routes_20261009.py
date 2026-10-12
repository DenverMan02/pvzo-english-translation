"""Prepend the original QuestHelper route to one method; preserve every old body.

No class recompilation: inherited AVM2 scopes, exceptions, and old branch offsets
are retained. All original constant pool bytes are copied exactly.
"""
from pathlib import Path
import hashlib
import json
import struct
import sys
import zlib

SERVER = Path('D:/pvzol本地最新最新ol/pvzol本地/PvZ-Server-PoC')
sys.path.insert(0, str(SERVER/'client_patches/endless-verifyerror-20261009'))
from abc_tools import parse_abc, find_abc, u30, s32, encode_u30

TARGET = ('com.qq.modules.welfareHall.view.components.WelfareHallDailyActivityCellMediator', 'onEnterClick')
BACKUP = Path('D:/pvzol本地最新最新ol/修复备份/20261009-本轮问题修复/webroot/ModuleWelfareHall.swf')
HERE = Path(__file__).resolve().parent


def unpack(path):
    packed = Path(path).read_bytes()
    raw = packed[:8] + zlib.decompress(packed[8:]) if packed[:3] == b'CWS' else packed
    assert len(raw) == int.from_bytes(raw[4:8], 'little')
    return raw


def spans(data):
    pos = 4
    for _ in range(2):
        count, pos = u30(data, pos)
        for _ in range(max(0, count-1)):
            pos = s32(data, pos)
    count, pos = u30(data, pos)
    pos += max(0, count-1)*8
    string_start = pos
    count, pos = u30(data, pos)
    string_entries = pos
    for _ in range(max(0, count-1)):
        size, pos = u30(data, pos); pos += size
    namespace_start = pos
    count, pos = u30(data, pos)
    namespace_entries = pos
    for _ in range(max(0, count-1)):
        _, pos = u30(data, pos+1)
    nsset_start = pos
    count, pos = u30(data, pos)
    for _ in range(max(0, count-1)):
        size, pos = u30(data, pos)
        for _ in range(size):
            _, pos = u30(data, pos)
    mn_start = pos
    count, pos = u30(data, pos)
    mn_entries = pos
    for _ in range(max(0, count-1)):
        kind = data[pos]; pos += 1
        if kind in (7, 13, 9, 14):
            _, pos = u30(data, pos); _, pos = u30(data, pos)
        elif kind in (15, 16, 27, 28):
            _, pos = u30(data, pos)
        elif kind in (17, 18):
            pass
        elif kind == 29:
            _, pos = u30(data, pos); size, pos = u30(data, pos)
            for _ in range(size):
                _, pos = u30(data, pos)
        else:
            raise ValueError(kind)
    return string_start, string_entries, namespace_start, namespace_entries, nsset_start, mn_start, mn_entries, pos


def build(source=BACKUP, destination=None):
    raw = unpack(source)
    begin, end, taglen_at, header_size, taglen = find_abc(raw)
    assert header_size == 6
    abc = raw[begin:end]
    before = parse_abc(abc)
    strings = list(before.strings)
    namespaces = list(before.namespaces)
    multinames = list(before.multinames)
    add_strings, add_namespaces, add_multinames = bytearray(), bytearray(), bytearray()

    def string_id(value):
        if value not in strings:
            encoded = value.encode('utf-8')
            add_strings.extend(encode_u30(len(encoded))); add_strings.extend(encoded)
            strings.append(value)
        return strings.index(value)

    def mn(name, package=''):
        nsname, strname = string_id(package), string_id(name)
        namespace = (22, nsname)
        if namespace not in namespaces:
            namespaces.append(namespace)
            add_namespaces.append(22); add_namespaces.extend(encode_u30(nsname))
        ns = namespaces.index(namespace)
        qname = (7, ns, strname)
        if qname not in multinames:
            multinames.append(qname)
            add_multinames.append(7); add_multinames.extend(encode_u30(ns)); add_multinames.extend(encode_u30(strname))
        return multinames.index(qname)

    key = string_id('localQuestJump')
    quest_helper = mn('QuestHelper', 'com.qq.modules.quest.logic')
    quest_jump = mn('questJump')
    private_obj = next(i for i, item in enumerate(before.multinames) if item and item[0] in (7,13)
                       and before.strings[item[2]] == 'm_clientParaObj')
    # Reuse the same dynamic property namespace as the original obj['type'].
    mid = before.class_methods[TARGET]
    _, old_at, old_length = before.method_bodies[mid]
    old_code = abc[old_at:old_at+old_length]
    type_at = old_code.index(b'\x2c' + encode_u30(before.strings.index('type')))
    at = type_at+1
    _, at = u30(old_code, at)
    assert old_code[at] == 0x66
    dynamic, _ = u30(old_code, at+1)
    code = bytearray([0xd0, 0x30, 0xd0, 0x66])
    code.extend(encode_u30(private_obj)); code.append(0x2c); code.extend(encode_u30(key))
    code.append(0x66); code.extend(encode_u30(dynamic)); code.append(0x2a)
    code.append(0x12); jump_at=len(code); code.extend(b'\0'*3)
    code.append(0x60); code.extend(encode_u30(quest_helper)); code.append(0x2b)
    code.append(0x4f); code.extend(encode_u30(quest_jump)); code.extend(encode_u30(1)); code.append(0x47)
    fallback=len(code); code.extend([0x29,0x1d])
    code[jump_at:jump_at+3]=(fallback-(jump_at+3)).to_bytes(3,'little',signed=True)
    prefix_size=len(code)
    code.extend(old_code)
    sstart, sentries, nsstart, nsentries, nssetstart, mnstart, mnentries, mned = spans(abc)
    abc=(abc[:sstart]+encode_u30(len(strings))+abc[sentries:nsstart]+bytes(add_strings)
         +encode_u30(len(namespaces))+abc[nsentries:nssetstart]+bytes(add_namespaces)
         +abc[nssetstart:mnstart]+encode_u30(len(multinames))+abc[mnentries:mned]
         +bytes(add_multinames)+abc[mned:])
    info=parse_abc(abc)
    header, maxstack, locals_, scope, maxscope=info.method_headers[mid]
    _, at, size=info.method_bodies[mid]
    exception_count, _=u30(abc,at+size)
    assert exception_count == 0, 'prepend would need exception offset relocation'
    header_bytes=b''.join(encode_u30(i) for i in (mid,max(maxstack,2),locals_,scope,maxscope,len(code)))
    abc=abc[:header]+header_bytes+bytes(code)+abc[at+size:]
    after=parse_abc(abc)
    assert before.class_methods == after.class_methods
    assert before.multinames == after.multinames[:len(before.multinames)]
    assert before.namespaces == after.namespaces[:len(before.namespaces)]
    for method,(_,p,n) in before.method_bodies.items():
        _,newp,newn=after.method_bodies[method]
        original=raw[begin:end][p:p+n]
        if method == mid:
            assert abc[newp+prefix_size:newp+newn] == original
        else:
            assert abc[newp:newp+newn] == original,method
        assert before.method_headers[method][2:] == after.method_headers[method][2:],method
    result=bytearray(raw[:begin]+abc+raw[end:])
    struct.pack_into('<I',result,taglen_at,taglen+len(abc)-(end-begin))
    struct.pack_into('<I',result,4,len(result));result[:3]=b'CWS'
    output=bytes(result[:8])+zlib.compress(result[8:],9)
    destination=Path(destination or HERE/'ModuleWelfareHall.daily-routes.swf')
    destination.write_bytes(output)
    manifest={'source':str(source),'output':str(destination),'sha256':hashlib.sha256(output).hexdigest(),
              'md5':hashlib.md5(output).hexdigest(),'size':len(output),'method_id':mid,
              'prefix_size':prefix_size,'old_body_preserved':True,'unchanged_methods':len(before.method_bodies)-1}
    (HERE/'daily_routes_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest


if __name__ == '__main__':
    build()
