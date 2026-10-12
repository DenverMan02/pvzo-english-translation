"""Restore the original bounded paid reset; preserve all other current AVM2 bodies."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import struct
import sys
import zlib

SERVER = Path('D:/pvzol本地最新最新ol/pvzol本地/PvZ-Server-PoC')
WEB = SERVER.parent/'webroot'
BACKUP = SERVER.parent.parent/'修复备份/20261009-本轮问题修复/endless-paid-reset'
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER/'client_patches/endless-verifyerror-20261009'))
from abc_tools import parse_abc, find_abc, u30, s32, encode_u30

TARGET = ('com.qq.modules.endlessFuben.view.EndlessFubenViewMediator', 'onClickBtnRefresh')
ORIGINAL_TOOLTIP = "剩余次数：<font color='{3}'>{0}</font>/{1}<br>金钻消耗：{2}"


def unpack(data):
    assert data[:3] in (b'CWS', b'FWS')
    raw = data[:8]+zlib.decompress(data[8:]) if data[:3] == b'CWS' else data
    assert len(raw) == int.from_bytes(raw[4:8], 'little')
    return raw


def patch_strings(abc, replacements):
    pos = 4
    for _ in range(2):
        count, pos = u30(abc, pos)
        for _ in range(max(0, count-1)):
            pos = s32(abc, pos)
    count, pos = u30(abc, pos); pos += max(0, count-1)*8
    count, pos = u30(abc, pos)
    entries_start = pos; chunks = []
    for index in range(1, count):
        start = pos
        length, pos = u30(abc, pos)
        pos += length
        if index in replacements:
            encoded = replacements[index].encode('utf-8')
            chunks.append(encode_u30(len(encoded))+encoded)
        else:
            chunks.append(abc[start:pos])
    return abc[:entries_start]+b''.join(chunks)+abc[pos:]


class Assembler:
    def __init__(self):
        self.code = bytearray(); self.labels = {}; self.fixups = []
    def op(self, opcode, *args):
        self.code.append(opcode)
        for value in args:
            self.code.extend(encode_u30(value))
        return self
    def label(self, name):
        self.labels[name] = len(self.code); return self
    def branch(self, opcode, label):
        self.code.append(opcode); pos = len(self.code)
        self.code.extend(b'\x00'*3); self.fixups.append((pos, label)); return self
    def finish(self):
        for pos, label in self.fixups:
            delta = self.labels[label] - (pos+3)
            assert -(1<<23) <= delta < (1<<23)
            self.code[pos:pos+3] = (delta & 0xffffff).to_bytes(3, 'little')
        return bytes(self.code)


def build():
    source = WEB/'ModuleEndlessFuben.swf'
    packed = source.read_bytes(); raw = unpack(packed)
    begin, end, taglen_at, header_size, taglen = find_abc(raw)
    assert header_size == 6
    original = raw[begin:end]; before = parse_abc(original)
    tips = [(i, value) for i, value in enumerate(before.strings)
            if value.startswith('今日免费重置：')]
    assert len(tips) == 1, tips
    replacements = {tips[0][0]: ORIGINAL_TOOLTIP}
    for text in ('免费重置进度', '购买重置进度'):
        index = before.strings.index(text)
        replacements[index] = '重新挑战'
    abc = patch_strings(original, replacements); info = parse_abc(abc)
    def multiname(name, package=''):
        found = []
        for i, item in enumerate(info.multinames):
            if not item or item[0] not in (7, 13):
                continue
            namespace, string = item[1:]
            _, nsname = info.namespaces[namespace]
            if info.strings[string] == name and info.strings[nsname] == package:
                found.append(i)
        assert len(found) == 1, (name, package, found)
        return found[0]
    string = lambda text: info.strings.index(text)
    common = multiname('CommonCmd', 'com.qq.modules.main.command')
    factory = multiname('ServerConfigTemplateFactory', 'com.qq.modules.basic.model.templates')
    proxycmd = multiname('EndlessFubenProxyCmd', 'com.qq.modules.endlessFuben.command')
    click = Assembler()
    click.op(0xd0).op(0x30).op(0xd0).op(0x66, multiname('proxy')).op(0x66, multiname('model'))
    click.op(0x46, multiname('getLeftResetTimes'), 0).op(0x24, 0).branch(0x16, 'exhausted')
    click.op(0x60, common).op(0x2c, string('重新挑战'))
    click.op(0x60, factory).op(0x66, multiname('instance')).op(0x66, multiname('iNoEndFubenResetGoldTicket'))
    click.op(0x60, proxycmd).op(0x66, multiname('requestReset'))
    click.op(0x20).op(0x24, 0).op(0x26).op(0x4f, multiname('confirmUseDiamond'), 6).op(0x47)
    click.label('exhausted').op(0x60, common).op(0x2c, string('重置次数已用完'))
    click.op(0x28).op(0x28).op(0x24, 0).op(0x20).op(0x26).op(0x4f, multiname('txtFly'), 6).op(0x47)
    code = click.finish(); mid = info.class_methods[TARGET]
    _, code_at, old_length = info.method_bodies[mid]
    length_at = info.method_bodies[mid][0]
    exception_count, _ = u30(abc, code_at+old_length)
    assert exception_count == 0
    assert info.method_headers[mid][1] >= 7  # same old inherited-scope header
    abc = abc[:length_at]+encode_u30(len(code))+code+abc[code_at+old_length:]
    after = parse_abc(abc)
    assert before.class_methods == after.class_methods
    assert before.namespaces == after.namespaces and before.multinames == after.multinames
    assert len(before.strings) == len(after.strings)
    for index, text in enumerate(before.strings):
        assert after.strings[index] == replacements.get(index, text)
    assert before.method_bodies.keys() == after.method_bodies.keys()
    for method, (_, old_pos, old_size) in before.method_bodies.items():
        _, new_pos, new_size = after.method_bodies[method]
        assert before.method_headers[method][1:] == after.method_headers[method][1:]
        if method != mid:
            assert original[old_pos:old_pos+old_size] == abc[new_pos:new_pos+new_size], method
    assert not any('免费重置' in text or '每日0点恢复' in text for text in after.strings)
    result = bytearray(raw[:begin]+abc+raw[end:])
    struct.pack_into('<I', result, taglen_at, taglen+len(abc)-len(original))
    struct.pack_into('<I', result, 4, len(result)); result[:3] = b'CWS'
    output = bytes(result[:8])+zlib.compress(bytes(result[8:]), 9)
    destination = HERE/'ModuleEndlessFuben.paid-reset.swf'; destination.write_bytes(output)
    manifest = dict(source_sha256=hashlib.sha256(packed).hexdigest(),
                    patched_sha256=hashlib.sha256(output).hexdigest(),
                    md5=hashlib.md5(output).hexdigest(), size=len(output),
                    changed_method=dict(classname=TARGET[0], method=TARGET[1], id=mid),
                    body_count=len(before.method_bodies), unchanged_body_count=len(before.method_bodies)-1,
                    all_headers_unchanged=True, changed_string_indices=list(replacements),
                    tooltip=ORIGINAL_TOOLTIP)
    (HERE/'paid_reset_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return packed, output, manifest


def deploy(packed, output, manifest):
    source = WEB/'ModuleEndlessFuben.swf'
    assert source.read_bytes() == packed, 'canonical resource changed during patch build'
    version = WEB/'fileVer.xml'; versiontext = version.read_bytes()
    targets = [source] + [path for path in WEB.glob('ModuleEndlessFuben_*.swf')
                          if re.fullmatch(r'ModuleEndlessFuben_[0-9a-f]{32}\.swf', path.name)]
    BACKUP.mkdir(parents=True, exist_ok=True)
    for path in targets+[version]:
        saved = BACKUP/path.name
        if saved.exists():
            assert saved.read_bytes() == path.read_bytes(), 'backup exists with different data'
        else:
            shutil.copy2(path, saved)
    pattern = rb'<file\s+name="ModuleEndlessFuben\.swf"\s+size="[^\"]*"\s+ver="[^\"]*"\s*/>'
    entry = ('<file name="ModuleEndlessFuben.swf" size="%s" ver="%s" />' %
             (manifest['size'], manifest['md5'])).encode()
    updated, count = re.subn(pattern, lambda _: entry, versiontext)
    assert count == 1
    for path in targets:
        path.write_bytes(output)
    alias = WEB/('ModuleEndlessFuben_'+manifest['md5']+'.swf')
    assert not alias.exists() or alias.read_bytes() == output
    alias.write_bytes(output)
    assert version.read_bytes() == versiontext, 'version manifest changed concurrently'
    version.write_bytes(updated)
    for path in targets+[alias]:
        assert path.read_bytes() == output
    assert entry in version.read_bytes()
    print('Deployed paid reset to canonical, %s existing aliases, new hash alias, and fileVer.' % (len(targets)-1))


if __name__ == '__main__':
    packed, output, manifest = build()
    if '--deploy' in sys.argv:
        deploy(packed, output, manifest)
