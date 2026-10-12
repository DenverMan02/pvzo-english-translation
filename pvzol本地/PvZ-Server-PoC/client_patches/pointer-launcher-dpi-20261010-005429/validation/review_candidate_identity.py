"""Independent declaration and namespace review of current repair candidates.

This preserves namespace indexes as identities and compares complete declarations,
instead of treating same short names as interchangeable.
"""
from pathlib import Path
import hashlib
import json
import qname_identity as Q
from abc_tools import parse_abc

HERE = Path(__file__).resolve().parent
WEB = HERE.parent/'baseline'
WORK = HERE.parent
FILES = [('Game_p.swf', WORK/'tower/Game_p.tower.swf'),
         ('ModuleTD_p.swf', WORK/'tower/ModuleTD_p.tower.swf')]
FILES += [(name, WORK/'bag/candidates'/name) for name in
          ['ModuleAct3years.swf', 'ModuleBag.swf', 'ModuleCard.swf',
           'ModuleChat.swf', 'ModuleUnion.swf']]


def identities(issues):
    return {(v['abc'], v['method'], v['opcode'], v['multiname'],
             tuple(v['reference']['namespace']['identity']),
             v['reference']['name']) for v in issues}


reports = []
for filename, candidate in FILES:
    source = WEB/filename
    before_abcs, after_abcs = Q.load(source), Q.load(candidate)
    assert len(before_abcs) == len(after_abcs)
    changes = []
    for abc_index, (before, after) in enumerate(zip(before_abcs, after_abcs)):
        # Signature return types/defaults and metadata are covered by this
        # byte-identical prefix through the beginning of method_body_info.
        before_info, after_info = parse_abc(before.b), parse_abc(after.b)
        before_first = min(v[0] for v in before_info.method_headers.values())
        after_first = min(v[0] for v in after_info.method_headers.values())
        assert before.b[:before_first] == after.b[:after_first]
        for field in ['strings', 'namespaces', 'ns_sets', 'multinames',
                      'classes', 'scripts']:
            assert getattr(before, field) == getattr(after, field), (filename, field)
        assert len(before.bodies) == len(after.bodies)
        for old, new in zip(before.bodies, after.bodies):
            assert {k:v for k,v in old.items() if k != 'code'} == {
                k:v for k,v in new.items() if k != 'code'}, (filename, old['method'])
            if old['code'] != new['code']:
                changes.append({'abc':abc_index, 'method':old['method'],
                                'before_length':len(old['code']),
                                'after_length':len(new['code'])})
    baseline = Q.audit_file(source)
    repaired = Q.audit_file(candidate)
    for kind in ['unresolved_private_qnames', 'unresolved_protected_qnames']:
        assert identities(repaired[kind]) == identities(baseline[kind]), (filename, kind)
    reports.append({'file':filename, 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                    'candidate_sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    'declarations_types_defaults_metadata_byte_identical':True,
                    'class_inheritance_interfaces_traits_byte_identical':True,
                    'namespace_identity_and_constant_pools_unchanged':True,
                    'scope_locals_exceptions_traits_unchanged':True,
                    'introduced_unresolved_private_or_protected_references':0,
                    'existing_external_protected_references':len(repaired['unresolved_protected_qnames']),
                    'changed_bodies':changes})

out = HERE/'independent_candidate_identity.json'
out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'files':len(reports), 'changed_bodies':sum(len(v['changed_bodies']) for v in reports),
                  'introduced_identity_errors':0, 'report':str(out)}, ensure_ascii=False))
