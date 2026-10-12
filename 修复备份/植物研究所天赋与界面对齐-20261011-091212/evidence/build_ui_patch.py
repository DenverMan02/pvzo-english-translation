"""Patch only the research popup's existing geometry in the current SWFs."""
from pathlib import Path
import hashlib
import json
from abc_tools import find_abc, parse_abc, encode_u30
from avm2_validate import unpack_swf, patch_swf_bytes, BodyReplacement

ROOT = Path('D:/pvzol本地最新最新ol/pvzol本地/webroot')
WORK = Path(__file__).resolve().parent
CLS = 'com.qq.modules.td.view.components.'

def build(source):
    raw = unpack_swf(source)
    a,b,length_at,*_ = find_abc(raw)
    abc = raw[a:b]
    info = parse_abc(abc)
    def mn(name):
        hits=[i for i,m in enumerate(info.multinames) if m and m[0] in (7,13)
              and info.strings[m[2]]==name and info.namespaces[m[1]][0]==0x16
              and info.strings[info.namespaces[m[1]][1]]=='']
        assert len(hits)==1,(name,hits)
        return encode_u30(hits[0])
    def get(name):
        mid=info.class_methods[(CLS+name,'InitializeComponent')]
        _,at,n=info.method_bodies[mid]
        return mid,abc[at:at+n]
    mid,code=get('TalentAdvancedWindowCE')
    old=b'\xd0\x66'+mn('l_plantName')+b'\x25'+encode_u30(170)+b'\x61'+mn('x')
    new=b'\xd0\x66'+mn('l_plantName')+b'\x25'+encode_u30(177)+b'\x61'+mn('x')
    assert code.count(old)==1
    changes={mid:BodyReplacement(code.replace(old,new))}
    init,code=get('TalentAdvancedWindow')
    assert code[-1:]==b'\x47'
    # Reuse the existing fontsize/14 string entries and public scaling setter.
    append=(b'\xd0\x66'+mn('icon')+b'\x26\x61'+mn('scaleContent')+
            b'\xd0\x66'+mn('l_condition')+b'\x2c'+encode_u30(info.strings.index('fontSize'))+
            b'\x2c'+encode_u30(info.strings.index('14'))+b'\x4f'+mn('SetStyle')+b'\x02')
    changes[init]=BodyReplacement(code[:-1]+append+b'\x47')
    output,reports=patch_swf_bytes(raw,changes)
    result=WORK/'candidate'/source.name
    result.parent.mkdir(exist_ok=True)
    result.write_bytes(output)
    # No SWF assets/tags outside the original DoABC are altered.
    newraw=unpack_swf(result)
    na,nb,*_=find_abc(newraw)
    assert raw[8:length_at]==newraw[8:length_at]
    assert raw[length_at+4:a]==newraw[length_at+4:na]
    assert raw[b:]==newraw[nb:]
    manifest={'source':str(source),'output':str(result),
              'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'output_sha256':hashlib.sha256(output).hexdigest(),
              'md5':hashlib.md5(output).hexdigest(),
              'changed_method_ids':sorted(changes),'total_method_bodies':len(info.method_bodies),
              'unchanged_method_bodies':len(info.method_bodies)-2,
              'constant_pools_and_classes_preserved':True,'all_other_swf_tags_preserved':True,
              'title_x':177,'title_width':150,'popup_width':504,
              'icon':{'x':237,'y':101,'width':50,'height':50,'scaleContent':True},
              'condition':{'x':284,'width':200,'fontSize':14},'verification':reports}
    result.with_suffix('.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest

if __name__=='__main__':
    manifests=[build(ROOT/name) for name in ('Game_n.swf','Game_p.swf')]
    (WORK/'ui_manifest.json').write_text(json.dumps(manifests,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{'file':Path(x['source']).name,'changed':x['changed_method_ids'],
                      'untouched_methods':x['unchanged_method_bodies']} for x in manifests]))
