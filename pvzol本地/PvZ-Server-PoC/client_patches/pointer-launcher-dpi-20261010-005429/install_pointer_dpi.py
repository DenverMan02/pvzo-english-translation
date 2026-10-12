"""Prepare/review then install the verified pointer and launcher changes."""
from pathlib import Path
import argparse, datetime, hashlib, json, os, re, shutil, zlib

HERE = Path(__file__).resolve().parent
ROOT = Path('D:/pvzol本地最新最新ol')
PROJECT = ROOT / 'pvzol本地'
WEB = PROJECT / 'webroot'
PLAN = HERE / 'pointer_dpi_plan.json'

def sha(data): return hashlib.sha256(data).hexdigest()

def atomic(path, data):
    temp = path.with_name(path.name + '.pointer-dpi.tmp')
    path.parent.mkdir(parents=True, exist_ok=True)
    with temp.open('wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    temp.replace(path)

def prepare():
    targets = {}
    manifest = json.loads((HERE/'plant-alignment/install_manifest.json').read_text('utf-8'))
    xml = (WEB/'fileVer.xml').read_text('utf-8')
    for item in manifest:
        original = WEB/item['file']
        candidate = Path(item['candidate'])
        data = candidate.read_bytes()
        assert sha(original.read_bytes()) == item['input_sha256']
        assert sha(data) == item['output_sha256']
        assert len(data) == item['size']
        assert hashlib.md5(data).hexdigest() == item['output_md5']
        raw = data[:8]+zlib.decompress(data[8:]) if data[:3] == b'CWS' else data
        assert data[:3] in (b'CWS',b'FWS') and len(raw) == int.from_bytes(data[4:8],'little')
        aliases = [p for p in WEB.glob(original.stem+'_*.swf') if re.fullmatch(re.escape(original.stem)+r'_[0-9a-f]{32}\.swf',p.name)]
        for dest in [original, *aliases, WEB/(original.stem+'_'+item['output_md5']+'.swf')]:
            targets[dest] = candidate
        pattern = r'(<file\b[^>]*\bname="'+re.escape(item['file'])+r'"[^>]*\bsize=")\d+("[^>]*\bver=")[^"]+("[^>]*/>)'
        xml, count = re.subn(pattern, lambda m:m[1]+str(len(data))+m[2]+item['output_md5']+m[3],xml)
        assert count == 1
    new_xml = HERE/'fileVer.pointer-dpi.xml'
    new_xml.write_text(xml,encoding='utf-8')
    targets[WEB/'fileVer.xml'] = new_xml
    for name in ['ui_shell.py','launcher_dpi.py','launcher_desktop.py']:
        candidate = HERE/'dpi-fix/staged'/name
        assert candidate.is_file()
        targets[PROJECT/name] = candidate
    package = HERE/'dpi-fix/dist/PvZLauncherUI'
    assert (package/'PvZLauncherUI.exe').is_file()
    # Preserve extra pre-existing dependencies; copy only files the new package changes.
    for source in package.rglob('*'):
        if source.is_file():
            dest = PROJECT/'启动器界面'/source.relative_to(package)
            if not dest.is_file() or sha(source.read_bytes()) != sha(dest.read_bytes()):
                targets[dest] = source
    records = []
    for dest, source in targets.items():
        data = source.read_bytes()
        records.append(dict(path=str(dest),source=str(source),existed=dest.exists(),
            before_sha256=sha(dest.read_bytes()) if dest.exists() else None,
            after_sha256=sha(data),size=len(data)))
    result = dict(root=str(ROOT),created_at=datetime.datetime.now().isoformat(),files=records,swf_manifest=manifest)
    PLAN.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(plan=str(PLAN),changed_files=len(records),changed_ui_files=sum('启动器界面' in r['path'] for r in records)),ensure_ascii=False))

def install():
    plan = json.loads(PLAN.read_text('utf-8'))
    assert Path(plan['root']).resolve() == ROOT.resolve()
    records = plan['files']
    data_by_path = {}
    # All guards run before any product write.
    for record in records:
        dest, source = Path(record['path']), Path(record['source'])
        dest.resolve().relative_to(ROOT.resolve())
        data = source.read_bytes()
        assert sha(data) == record['after_sha256'] and len(data) == record['size'], str(source)
        assert dest.exists() == record['existed'], str(dest)
        assert (sha(dest.read_bytes()) if dest.exists() else None) == record['before_sha256'], str(dest)
        data_by_path[dest] = data
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = ROOT/'修复备份'/('种植坐标与启动器DPI-'+stamp)
    patch = PROJECT/'PvZ-Server-PoC/client_patches'/('pointer-launcher-dpi-'+stamp)
    assert not backup.exists() and not patch.exists()
    backup.mkdir(parents=True)
    for record in records:
        dest = Path(record['path'])
        if record['existed']:
            saved = backup/dest.relative_to(ROOT)
            saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(dest,saved)
    (backup/'安装记录.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    (backup/'恢复说明.txt').write_text('退出游戏和启动器后，将本备份目录pvzol本地中的文件复制回游戏同名目录即可恢复。新增launcher_dpi.py和哈希SWF可保留，旧程序不会调用它们。本次不修改账号、存档、数据库或系统显示缩放。\n',encoding='utf-8')
    try:
        for dest, data in data_by_path.items():
            if dest != WEB/'fileVer.xml': atomic(dest,data)
        atomic(WEB/'fileVer.xml',data_by_path[WEB/'fileVer.xml'])
        for record in records: assert sha(Path(record['path']).read_bytes()) == record['after_sha256']
    except BaseException:
        for record in records:
            dest = Path(record['path'])
            if record['existed']: atomic(dest,(backup/dest.relative_to(ROOT)).read_bytes())
            elif dest.exists(): dest.unlink()
        raise
    patch.mkdir(parents=True)
    for folder in ['plant-alignment','dpi-fix','dpi-fix/staged','dpi-tests','validation']:
        source_dir = HERE/folder
        target_dir = patch/folder
        target_dir.mkdir(parents=True,exist_ok=True)
        for source in source_dir.iterdir():
            if source.is_file() and source.suffix in ('.py','.json','.swf','.manifest','.ps1','.log','.md') and source.name != 'flashplayer_sa.exe':
                shutil.copy2(source,target_dir/source.name)
    for name in ['install_pointer_dpi.py','pointer_dpi_plan.json']:
        shutil.copy2(HERE/name,patch/name)
    result = dict(backup=str(backup),patch=str(patch),installed_at=stamp,count=len(records),files=records,
                  system_display_settings_changed=False,account_or_save_data_changed=False)
    (HERE/'pointer_dpi_installed.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (patch/'installed.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('backup','patch','count')},ensure_ascii=False))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',choices=['prepare','install'])
    args = parser.parse_args()
    (prepare if args.mode == 'prepare' else install)()
