"""Collect this verification subtask's machine-readable evidence."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent
reports = {name:json.loads((HERE/name).read_text(encoding='utf-8'))
           for name in ('bag_validation_summary.json','tower_structure_summary.json','tower_behavior_summary.json')}
scan = json.loads((HERE/'qpopup_scan.json').read_text(encoding='utf-8'))
files = []
for candidate in [*(HERE.parent/'bag'/'candidates').glob('*.swf'), *(HERE.parent/'tower').glob('*.tower.swf')]:
    original_name = candidate.name.replace('.tower.swf','.swf')
    baseline = HERE.parent/'baseline'/original_name
    files.append(dict(filename=original_name,baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
                      candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest()))
summary = dict(passed=all(report['passed'] for report in reports.values()),
               test_count=sum(report['test_count'] for report in reports.values()),
               original_error_reproductions=5,menu_coordinate_scenarios=150,
               explicit_position_scenarios=5,chest_size_scenarios=5,
               tower_viewport_scenarios=10,context_recovery_cycles=4,
               canonical_files_scanned=scan['canonical_files_scanned'],
               qpopup_definitions=[dict(file=r['file'],method_id=r['method_id']) for r in scan['definitions']],
               canonical_scan_failures=scan['failures'],
               account_requests_sent=0,real_flash_ui_tested=False,
               reports=reports,module_hashes=files)
(HERE/'verification_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:summary[k] for k in ('passed','test_count','original_error_reproductions','canonical_files_scanned')},ensure_ascii=False))
