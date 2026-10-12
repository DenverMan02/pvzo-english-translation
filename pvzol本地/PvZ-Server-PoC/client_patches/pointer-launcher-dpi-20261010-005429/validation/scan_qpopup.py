"""Read-only scan of canonical client SWFs for QPopupMenu definitions."""
from pathlib import Path
import importlib.util
import json
import re

VM_PATH = Path('C:/Users/komi/Documents/Codex/2026-10-08/d-pvzol-ol-3/work/window-resize/validation/avm2_resize_vm.py')
spec = importlib.util.spec_from_file_location('previous_resize_vm', VM_PATH)
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)
BASE = Path('D:/pvzol本地最新最新ol/pvzol本地/webroot')
HERE = Path(__file__).resolve().parent

def scan():
    results = []
    failures = []
    count = 0
    for path in sorted(BASE.glob('*.swf')):
        if re.search(r'_[0-9a-f]{32}\.swf$', path.name):
            continue
        count += 1
        try:
            abcs = [vm.audit.Abc(b).load(tries=10) for b in vm.audit.abc_blobs(str(path))]
            for ai, abc in enumerate(abcs):
                for entry in abc.classes:
                    if entry['name'] != 'com.qq.display::QPopupMenu':
                        continue
                    for trait in entry['traits'] + entry['inst_traits']:
                        if trait.get('method') is not None and vm.VM.short(trait['name']) == 'show':
                            body = abc.body_of_method[trait['method']]
                            results.append(dict(file=path.name, abc_index=ai, method_id=trait['method'],
                                                code_size=len(body['code']), body=body,
                                                disassembly=abc.disassemble(body['code'])))
        except Exception as exc:
            failures.append(dict(file=path.name, error=str(exc)))
    result = dict(canonical_files_scanned=count, definitions=results, failures=failures)
    (HERE/'qpopup_scan.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=lambda x:x.hex() if isinstance(x,bytes) else str(x)), encoding='utf-8')
    for record in results:
        print(record['file'], record['method_id'], record['code_size'])
        print('\n'.join(record['disassembly']))
    print(json.dumps(dict(scanned=count, definitions=len(results), failures=failures), ensure_ascii=False))
    return result

if __name__ == '__main__':
    scan()
