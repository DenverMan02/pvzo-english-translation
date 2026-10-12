"""Retain original AVM2 classes and patch only reset button/remaining-count bodies."""
from pathlib import Path
import hashlib
import importlib.util
import json
import struct
import sys
import zlib

HERE=Path(__file__).resolve().parent
SERVER=Path('D:/pvzol本地最新最新ol/pvzol本地/PvZ-Server-PoC')
SOURCE=Path('D:/pvzol本地最新最新ol/修复备份/20261009-详情头像与无尽客户端/webroot/ModuleEndlessFuben.swf')
parser_source=(SERVER/'client_patches/card-exp-bean-repeat-20260926/patch_module_card_clickthrough.py').read_text(encoding='utf-8')
parser_source=parser_source[:parser_source.index('\ndef patch_file(')]
parser_source=parser_source.replace('    strings: list[str]', '    method_headers: dict\n    strings: list[str]',1)
parser_source=parser_source.replace('    method_bodies = {}\n', '    method_bodies = {}\n    method_headers = {}\n',1)
old='''        method_index, pos = u30(data, pos)
        _, pos = u30(data, pos)  # max stack
        _, pos = u30(data, pos)  # local count
        _, pos = u30(data, pos)  # init scope depth
        _, pos = u30(data, pos)  # max scope depth'''
new='''        header_start = pos
        method_index, pos = u30(data, pos)
        max_stack, pos = u30(data, pos)
        local_count, pos = u30(data, pos)
        init_scope, pos = u30(data, pos)
        max_scope, pos = u30(data, pos)
        method_headers[method_index] = (header_start, max_stack, local_count, init_scope, max_scope)'''
assert old in parser_source
parser_source=parser_source.replace(old,new,1).replace('return ABCInfo(strings, namespaces, multinames, class_methods, method_bodies)', 'return ABCInfo(method_headers, strings, namespaces, multinames, class_methods, method_bodies)',1)
(HERE/'abc_tools.py').write_text(parser_source,encoding='utf-8')
sys.path.insert(0,str(HERE))
from abc_tools import parse_abc,find_abc,u30,s32,encode_u30

def unpack(path):
    packed=path.read_bytes()
    assert packed[:3] in (b'CWS',b'FWS')
    raw=packed[:8]+zlib.decompress(packed[8:]) if packed[:3]==b'CWS' else packed
    assert len(raw)==int.from_bytes(raw[4:8],'little')
    return raw

def strings_span(abc):
    p=4
    for _ in range(2):
        count,p=u30(abc,p)
        for _ in range(max(0,count-1)):p=s32(abc,p)
    count,p=u30(abc,p);p+=max(0,count-1)*8
    start=p;count,p=u30(abc,p)
    for _ in range(max(0,count-1)):
        size,p=u30(abc,p);p+=size
    return start,p

class Assembler:
    def __init__(self):self.code=bytearray();self.labels={};self.fixups=[]
    def op(self,opcode,*args):
        self.code.append(opcode)
        for a in args:self.code.extend(encode_u30(a))
        return self
    def label(self,name):self.labels[name]=len(self.code);return self
    def branch(self,opcode,name):
        self.code.append(opcode);at=len(self.code);self.code.extend(b'\x00'*3)
        self.fixups.append((at,name));return self
    def finish(self):
        for at,name in self.fixups:
            delta=self.labels[name]-(at+3)
            assert -(1<<23)<=delta<(1<<23)
            self.code[at:at+3]=(delta&0xffffff).to_bytes(3,'little')
        return bytes(self.code)

def build():
    raw=unpack(SOURCE)
    start,end,tag_length_at,header_size,tag_len=find_abc(raw)
    assert header_size==6
    original=raw[start:end];before=parse_abc(original)
    strings=list(before.strings)
    old_tip=[i for i,s in enumerate(strings) if s.startswith('剩余次数：<font') and '{2}' in s and '{3}' in s]
    assert len(old_tip)==1
    strings[old_tip[0]]="今日免费重置：<font color='{3}'>{0}</font>/{1}<br>每日0点恢复免费次数；用完后每次{2}钻购买重置"
    def string_id(value):
        if value not in strings:strings.append(value)
        return strings.index(value)
    free=string_id('免费重置进度');paid=string_id('购买重置进度')
    cp=bytearray(encode_u30(len(strings)))
    for s in strings[1:]:
        b=s.encode('utf-8');cp.extend(encode_u30(len(b)));cp.extend(b)
    sa,sb=strings_span(original)
    abc=original[:sa]+bytes(cp)+original[sb:]
    info=parse_abc(abc)
    def mn(name,package=None):
        found=[]
        for i,n in enumerate(info.multinames):
            if not n or n[0] not in (7,13):continue
            ns,si=n[1:];_,nsi=info.namespaces[ns]
            if info.strings[si]==name and (package is None or info.strings[nsi]==package):found.append(i)
        assert len(found)==1,(name,package,found)
        return found[0]
    factory=mn('ServerConfigTemplateFactory','com.qq.modules.basic.model.templates')
    instance=mn('instance','');price=mn('iNoEndFubenResetGoldTicket','')
    common=mn('CommonCmd','com.qq.modules.main.command')
    proxycmd=mn('EndlessFubenProxyCmd','com.qq.modules.endlessFuben.command')
    click=Assembler()
    click.op(0xd0).op(0x30).op(0xd0).op(0x66,mn('proxy','')).op(0x66,mn('model','')).op(0x46,mn('getLeftResetTimes',''),0)
    click.op(0x24,0).branch(0x17,'free')
    click.op(0x60,factory).op(0x66,instance).op(0x66,price).op(0x73).op(0xd6).branch(0x10,'confirm')
    click.label('free').op(0x24,0).op(0xd6)
    click.label('confirm').op(0x60,common).op(0xd2).op(0x24,0).branch(0x17,'paid_label')
    click.op(0x2c,free).branch(0x10,'args')
    click.label('paid_label').op(0x2c,paid)
    click.label('args').op(0xd2).op(0x60,proxycmd).op(0x66,mn('requestReset',''))
    click.op(0x20).op(0x24,0).op(0x26).op(0x4f,mn('confirmUseDiamond',''),6).op(0x47)
    left=Assembler()
    left.op(0xd0).op(0x30).op(0x60,factory).op(0x66,instance).op(0x66,mn('iNoEndFubenResetTimes',''))
    left.op(0xd0).op(0x66,mn('resetTimes','')).op(0xa1).op(0x2a).op(0x24,0).branch(0x18,'return')
    left.op(0x29).op(0x24,0)
    left.label('return').op(0x73).op(0x48)
    targets={
      ('com.qq.modules.endlessFuben.view.EndlessFubenViewMediator','onClickBtnRefresh'):(click.finish(),7,3),
      ('com.qq.modules.endlessFuben.model.EndlessFubenModel','getLeftResetTimes'):(left.finish(),3,1),
    }
    patches=[]
    for target,(code,max_stack,local_count) in targets.items():
        mid=info.class_methods[target]
        header,_,_,scope,max_scope=info.method_headers[mid]
        _,code_at,size=info.method_bodies[mid]
        prefix=b''.join(encode_u30(x) for x in (mid,max_stack,local_count,scope,max_scope,len(code)))
        patches.append((header,code_at+size,prefix+code,mid))
    for a,b,replacement,_ in sorted(patches,reverse=True):abc=abc[:a]+replacement+abc[b:]
    after=parse_abc(abc)
    changed={mid for _,_,_,mid in patches}
    assert before.class_methods==after.class_methods
    assert before.namespaces==after.namespaces and before.multinames==after.multinames
    assert before.method_bodies.keys()==after.method_bodies.keys()
    for mid,(_,p,n) in before.method_bodies.items():
        _,ap,an=after.method_bodies[mid]
        if mid not in changed:
            assert original[p:p+n]==abc[ap:ap+an],mid
            assert before.method_headers[mid][1:]==after.method_headers[mid][1:],mid
    result=bytearray(raw[:start]+abc+raw[end:])
    struct.pack_into('<I',result,tag_length_at,tag_len+len(abc)-len(original))
    struct.pack_into('<I',result,4,len(result))
    result[:3]=b'CWS'
    output=bytes(result[:8])+zlib.compress(bytes(result[8:]),9)
    dest=HERE/'ModuleEndlessFuben.minimal.swf';dest.write_bytes(output)
    manifest=dict(original_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                  patched_sha256=hashlib.sha256(output).hexdigest(),
                  methods=[dict(classname=c,name=m,id=before.class_methods[(c,m)]) for c,m in targets],
                  body_count=len(before.method_bodies),unchanged_body_count=len(before.method_bodies)-2,
                  changed_tooltip_string_index=old_tip[0],appended_strings=[free,paid])
    (HERE/'minimal_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__':build()
