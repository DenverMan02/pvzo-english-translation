"""Patch existing instruction operands only; leave ABC layout and branches intact."""
from pathlib import Path
import sys,zlib,hashlib,json,re
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'validation'))
import original_abc_audit as A
A.ns['swf_body']=lambda p: zlib.decompress(Path(p).read_bytes()[8:]) if Path(p).read_bytes()[:3]==b'CWS' else Path(p).read_bytes()[8:]
WEB=Path('D:/pvzol本地最新最新ol/pvzol本地/webroot')
HERE=Path(__file__).resolve().parent
OUT=HERE/'candidates'; OUT.mkdir(exist_ok=True)

def u30(n):
    out=bytearray()
    while n>=128: out.append((n&127)|128);n>>=7
    out.append(n);return bytes(out)

def operand(a,name):
    matches=[i for i in range(1,len(a.multinames)) if a.mn(i)==name]
    assert len(matches)==1,(name,matches)
    return u30(matches[0])

def code(a,classname,method):
    c=next(c for c in a.classes if c['name']==classname)
    mid=next(m for n,m in a.trait_methods(c,method) if n.endswith('::'+method))
    return mid,a.body_of_method[mid]['code']

manifest=[]
for fname in ['ModuleAct3years.swf','ModuleBag.swf','ModuleCard.swf','ModuleChat.swf','ModuleUnion.swf']:
    p=WEB/fname;original=p.read_bytes()
    raw=original[:8]+zlib.decompress(original[8:]) if original[:3]==b'CWS' else original
    abcs=A.load(str(p));a=next(a for a in abcs if any(c['name']=='com.qq.display::QPopupMenu' for c in a.classes))
    mid,old=code(a,'com.qq.display::QPopupMenu','show')
    new=old
    stage=b'\x66'+operand(a,'::stage')
    for axis in ['X','Y']:
        seq=b'\x60'+operand(a,'asgui.core::Application')+b'\x66'+operand(a,'::application')+stage+b'\x66'+operand(a,'::mouse'+axis)
        assert new.count(seq)==1,(fname,axis)
        new=new.replace(seq,seq.replace(stage,b'\x02'*len(stage)),1)
    assert len(old)==len(new) and a.b.count(old)==1
    blob=a.b.replace(old,new,1)
    changes=[{'class':'com.qq.display.QPopupMenu','method':'show','method_id':mid,'old_length':len(old),'new_length':len(new),'change':'read Application local mouse coordinates'}]
    if fname=='ModuleBag.swf':
        mid,old=code(a,'com.qq.modules.bag.view.components::BagItemOpenEffectViewMediator','init');new=old
        for dimension in ['Width','Height']:
            seq=b'\x60'+operand(a,'com.qq::GameGloble')+b'\x66'+operand(a,'::stage')+b'\x66'+operand(a,'::stage'+dimension)
            replacement=b'\x60'+operand(a,'asgui.core::Application')+b'\x66'+operand(a,'::application')+b'\x66'+operand(a,'::'+dimension.lower())
            assert len(seq)==len(replacement) and new.count(seq)==1
            new=new.replace(seq,replacement,1)
        assert a.b.count(old)==1
        blob=blob.replace(old,new,1)
        changes.append({'class':'com.qq.modules.bag.view.components.BagItemOpenEffectViewMediator','method':'init','method_id':mid,'old_length':len(old),'new_length':len(new),'change':'size open effect using logical application dimensions'})
    assert len(blob)==len(a.b) and raw.count(a.b)==1
    packed=raw.replace(a.b,blob,1)
    packed=packed[:8]+zlib.compress(packed[8:]) if original[:3]==b'CWS' else packed
    out=OUT/fname;out.write_bytes(packed)
    after=A.load(str(out))
    expected={e['method_id'] for e in changes}
    differences=[]
    for before_abc,after_abc in zip(abcs,after):
        assert len(before_abc.bodies)==len(after_abc.bodies)
        for before,after_body in zip(before_abc.bodies,after_abc.bodies):
            assert before['method']==after_body['method']
            if before['code']!=after_body['code']:differences.append(before['method'])
    assert set(differences)==expected
    manifest.append({'file':fname,'input_sha256':hashlib.sha256(original).hexdigest(),'output_sha256':hashlib.sha256(packed).hexdigest(),'output_md5':hashlib.md5(packed).hexdigest(),'size':len(packed),'candidate':str(out),'changes':changes,'unrelated_methods_unchanged':True,'constant_pools_signatures_scripts_and_exception_tables_unchanged':True})
    print(fname,changes)
(HERE/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
