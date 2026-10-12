"""Center the 67px portrait inside its 80px frame, preserving other ABC code."""
from pathlib import Path
import sys, struct, zlib, hashlib, json, shutil, datetime

ROOT = Path(r'D:\pvzol本地最新最新ol\pvzol本地')
WORK = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'PvZ-Server-PoC/client_patches/card-exp-bean-repeat-20260926'))
from patch_module_card_clickthrough import find_abc, parse_abc, u30, encode_u30
TARGET = ('com.qq.modules.mapinfo.view.components.MapInfoRoleWndCE', 'InitializeComponent')

def unpack(path):
    packed = path.read_bytes()
    assert packed[:3] in (b'CWS', b'FWS')
    raw = packed[:8] + zlib.decompress(packed[8:]) if packed[:3] == b'CWS' else packed
    assert len(raw) == int.from_bytes(raw[4:8], 'little')
    return packed, raw

def name_indexes(info, name):
    return [i for i,m in enumerate(info.multinames) if m and m[0] in (7,13) and info.strings[m[2]] == name]

def patch(source, destination):
    packed, raw = unpack(source)
    start, end, tag_length_at, header_size, tag_length = find_abc(raw)
    assert header_size == 6
    abc = raw[start:end]
    info = parse_abc(abc)
    mid = info.class_methods[TARGET]
    length_at, code_at, code_length = info.method_bodies[mid]
    assert u30(abc, code_at+code_length)[0] == 0, 'Unexpected exception table'
    pos = 4
    for _ in range(2):
        count,pos = u30(abc,pos)
        for _ in range(max(0,count-1)):
            _,pos = u30(abc,pos)
    count,pos = u30(abc,pos)
    double_indexes = [i+1 for i in range(max(0,count-1)) if struct.unpack_from('<d',abc,pos+8*i)[0] == 6.5]
    assert len(double_indexes) == 1
    value = b'\x2f' + encode_u30(double_indexes[0])
    code = abc[code_at:code_at+code_length]
    replacements = []
    for prop in ('x','y'):
        hits = []
        for portrait in name_indexes(info, 'imgRole'):
            for prop_index in name_indexes(info, prop):
                prefix = b'\xd0\x66' + encode_u30(portrait)
                suffix = b'\x61' + encode_u30(prop_index)
                old = prefix+b'\x24\x05'+suffix
                at = code.find(old)
                if at >= 0:
                    hits.append((at,old,prefix+value+suffix))
        assert len(hits) == 1, (source.name,prop,hits)
        replacements.extend(hits)
    new_code = code
    for at,old,new in sorted(replacements, reverse=True):
        assert new_code[at:at+len(old)] == old
        new_code = new_code[:at]+new+new_code[at+len(old):]
    # The generated CE initialization has no branches: validate with a real
    # decoder, rather than searching raw bytes which can also be operands.
    sys.path.insert(0, str(ROOT/'PvZ-Server-PoC/client_tools'))
    from abcdis import OPS
    cursor = 0
    while cursor < len(code):
        op=code[cursor]; cursor+=1
        name,fmt=OPS[op]
        assert fmt not in ('j','CASE'), ('Unexpected branch',name)
        if fmt == 'b': cursor+=1
        elif fmt in ('u','m','s','i','d','n'): _,cursor=u30(code,cursor)
        elif fmt in ('mu','um','uu'): _,cursor=u30(code,cursor); _,cursor=u30(code,cursor)
        elif fmt == 'DEBUG': cursor+=1; _,cursor=u30(code,cursor); cursor+=1; _,cursor=u30(code,cursor)
        else: assert fmt == '', (name,fmt)
    assert cursor == len(code)
    # Length encoding is directly followed by the code.
    assert code_at == length_at+len(encode_u30(code_length))
    new_abc = abc[:length_at] + encode_u30(len(new_code)) + new_code + abc[code_at+code_length:]
    delta = len(new_abc)-len(abc)
    output_raw = bytearray(raw[:start]+new_abc+raw[end:])
    struct.pack_into('<I', output_raw, tag_length_at, tag_length+delta)
    struct.pack_into('<I', output_raw, 4, len(output_raw))
    new_info = parse_abc(new_abc)
    assert new_info.class_methods == info.class_methods
    assert new_info.strings == info.strings and new_info.multinames == info.multinames
    assert set(new_info.method_bodies) == set(info.method_bodies)
    changed=[]
    for method_id,(_,offset,size) in info.method_bodies.items():
        _,new_offset,new_size = new_info.method_bodies[method_id]
        if abc[offset:offset+size] != new_abc[new_offset:new_offset+new_size]:
            changed.append(method_id)
    assert changed == [mid], changed
    # Every byte outside this method's code and length remains unchanged in ABC.
    _,new_code_at,new_size=new_info.method_bodies[mid]
    assert abc[:length_at] == new_abc[:length_at]
    assert abc[code_at+code_length:] == new_abc[new_code_at+new_size:]
    output = bytes(output_raw[:8])+zlib.compress(bytes(output_raw[8:]),9) if packed[:3]==b'CWS' else bytes(output_raw)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes(output)
    return {'source':source.name,'sha256_before':hashlib.sha256(packed).hexdigest(),
            'sha256_after':hashlib.sha256(output).hexdigest(),'changed_method':'.'.join(TARGET),
            'changed_method_id':mid,'total_method_bodies_verified':len(info.method_bodies),
            'before':{'x':5,'y':5},'after':{'x':6.5,'y':6.5},'abc_growth_bytes':delta}

def stage():
    result=[]
    for filename in ('Game_p.swf','Game_n.swf'):
        result.append(patch(ROOT/'webroot'/filename,WORK/'avatar_staged'/filename))
    (WORK/'avatar_staged/verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

def install():
    manifest=json.loads((WORK/'avatar_staged/verification.json').read_text(encoding='utf-8'))
    backup=ROOT.parent/'修复备份'/('20261009-详情头像精确居中-'+datetime.datetime.now().strftime('%H%M%S'))
    destinations=[]
    for record in manifest:
        canonical=record['source']
        candidates=list((ROOT/'webroot').glob(canonical.replace('.swf','*.swf')))
        if canonical=='Game_n.swf':candidates.append(ROOT/'webroot/Game.swf')
        for path in candidates:
            assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256_before'], 'Source changed: '+str(path)
            destinations.append((path,WORK/'avatar_staged'/canonical,record))
    backup.mkdir(parents=True,exist_ok=False)
    for path,_,_ in destinations:
        shutil.copy2(path,backup/path.name)
    updates=[]
    for path,candidate,record in destinations:
        shutil.copy2(candidate,path)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256_after']
        updates.append({'file':str(path),'old_sha256':record['sha256_before'],'new_sha256':record['sha256_after']})
    patch_dir=ROOT/'PvZ-Server-PoC/client_patches/avatar-centering-20261009'
    patch_dir.mkdir(parents=True,exist_ok=True)
    shutil.copy2(__file__,patch_dir/'center_avatar.py')
    source=WORK/'avatar_after/scripts/com/qq/modules/mapinfo/view/components/MapInfoRoleWndCE.as'
    shutil.copy2(source,patch_dir/source.name)
    result={'backup':str(backup),'changes':updates,'verification':manifest}
    (patch_dir/'updated_files.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (WORK/'avatar_installed.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Installed',len(updates),'SWF aliases. Backup:',backup)

if __name__=='__main__':
    install() if '--install' in sys.argv else stage()
