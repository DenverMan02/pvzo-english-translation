"""Install checked local client resources, with backups and version aliases."""
from pathlib import Path
import json,hashlib,shutil,re,datetime,os,zlib

ROOT=Path('D:/pvzol本地最新最新ol')
WEB=ROOT/'pvzol本地/webroot'
HERE=Path(__file__).resolve().parent
STAMP=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
BACKUP=ROOT/'修复备份'/('塔防全屏与道具位置-'+STAMP)
PATCH=ROOT/'pvzol本地/PvZ-Server-PoC/client_patches'/('tower-fullscreen-item-position-'+STAMP)

def sha_bytes(data):return hashlib.sha256(data).hexdigest()
def sha(path):return sha_bytes(path.read_bytes())
def atomic_bytes(target,data):
    tmp=target.with_name(target.name+'.fullscreen-item-fix.tmp')
    with tmp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    tmp.replace(target)

def main():
    # Canonical resources prepared and verified separately. Input hashes guard
    # against installing over a concurrent repair from another chat.
    manifests=json.loads((HERE/'bag/manifest.json').read_text(encoding='utf-8'))
    manifests+=json.loads((HERE/'tower/install_manifest.json').read_text(encoding='utf-8'))
    xmlfile=WEB/'fileVer.xml';xml=xmlfile.read_bytes().decode('utf-8');newxml=xml
    targets={};canonical={}
    for m in manifests:
        name=m['file'];p=WEB/name;source=Path(m['candidate']);data=source.read_bytes()
        assert sha(p)==m['input_sha256'],f'Client changed after audit: {name}'
        assert sha_bytes(data)==m['output_sha256'],f'Candidate changed: {name}'
        assert len(data)==m['size'] and hashlib.md5(data).hexdigest()==m['output_md5']
        raw=data[:8]+zlib.decompress(data[8:]) if data[:3]==b'CWS' else data
        assert data[:3] in (b'CWS',b'FWS') and len(raw)==int.from_bytes(data[4:8],'little')
        canonical[name]=m
        aliases=[q for q in WEB.glob(p.stem+'_*.swf') if re.fullmatch(re.escape(p.stem)+r'_[0-9a-f]{32}\.swf',q.name)]
        for q in [p,*aliases,WEB/(p.stem+'_'+m['output_md5']+'.swf')]:targets[q]=data
        pattern=r'(<file\b[^>]*\bname="'+re.escape(name)+r'"[^>]*\bsize=")\d+("[^>]*\bver=")[^"]+("[^>]*/>)'
        newxml,count=re.subn(pattern,lambda match:match[1]+str(len(data))+match[2]+m['output_md5']+match[3],newxml)
        assert count==1,('fileVer canonical mapping',name,count)
    targets[xmlfile]=newxml.encode('utf-8')
    assert not BACKUP.exists() and not PATCH.exists()
    (BACKUP/'webroot').mkdir(parents=True)
    records=[]
    for p,data in targets.items():
        exists=p.exists();old=p.read_bytes() if exists else None
        if exists:shutil.copy2(p,BACKUP/'webroot'/p.name)
        records.append({'path':str(p),'existed':exists,'before_sha256':sha_bytes(old) if exists else None,'after_sha256':sha_bytes(data)})
    (BACKUP/'安装记录.json').write_text(json.dumps({'files':records,'canonical':canonical},ensure_ascii=False,indent=2),encoding='utf-8')
    (BACKUP/'恢复说明.txt').write_text('完全退出Flash游戏后，将本目录webroot中的文件复制回游戏pvzol本地/webroot同名文件即可恢复。新增哈希别名可保留，旧fileVer.xml不会加载它们。\n本次仅改客户端SWF和版本索引，不修改玩家数据库或服务端。\n',encoding='utf-8')
    try:
        # Publish every versioned binary before its new version-index mapping.
        for p,data in targets.items():
            if p!=xmlfile:atomic_bytes(p,data)
        atomic_bytes(xmlfile,targets[xmlfile])
        for record in records:assert sha(Path(record['path']))==record['after_sha256']
    except BaseException:
        for record in records:
            p=Path(record['path'])
            if record['existed']:atomic_bytes(p,(BACKUP/'webroot'/p.name).read_bytes())
            elif p.exists():p.unlink()
        raise
    PATCH.mkdir(parents=True)
    for m in manifests:shutil.copy2(m['candidate'],PATCH/m['file'])
    for folder in ['bag','tower','validation']:
        target=PATCH/folder;target.mkdir()
        for p in (HERE/folder).glob('*.py'):shutil.copy2(p,target/p.name)
        for p in (HERE/folder).glob('*.json'):shutil.copy2(p,target/p.name)
        for p in (HERE/folder).glob('*.log'):shutil.copy2(p,target/p.name)
    shutil.copytree(HERE/'baseline',PATCH/'baseline')
    shutil.copytree(HERE/'bag/candidates',PATCH/'bag/candidates')
    for name in ['Game_p.tower.swf','ModuleTD_p.tower.swf']:
        shutil.copy2(HERE/'tower'/name,PATCH/'tower'/name)
    shutil.copy2(HERE/'install_client_fixes.py',PATCH/'install_client_fixes.py')
    result={'backup':str(BACKUP),'patch':str(PATCH),'files':records,'canonical_files':list(canonical),'count':len(records),'installed_at':STAMP,'save_or_server_files_modified':False}
    (HERE/'installed.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (PATCH/'installed.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['backup','patch','canonical_files','count']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
