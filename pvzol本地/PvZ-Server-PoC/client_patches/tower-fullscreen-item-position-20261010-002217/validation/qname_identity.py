"""Check ABC member references without collapsing namespace identity.

PrivateNamespace entries are unique identities within one ABC constant pool,
including entries having identical or empty URI strings. Public/package names
use kind + URI. This checker is intentionally stricter than the scratch VM's
short-name native mocks.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import zlib
from collections import defaultdict
from collections import Counter
from pathlib import Path

AUDIT = Path(__file__).resolve().with_name("original_abc_audit.py")
spec = importlib.util.spec_from_file_location("identity_audit", AUDIT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
audit.ns["swf_body"] = lambda path: (lambda raw: zlib.decompress(raw[8:]) if raw[:3]==b"CWS" else raw[8:])(Path(path).read_bytes())
Reader = audit.Reader
OPS = audit.ns["OPS"]


class ABC(audit.Abc):
    def _traits(self, reader):
        entries = []
        for _ in range(reader.u30()):
            name, flags = reader.u30(), reader.u8()
            kind = flags & 15
            trait = dict(name=self.mn(name), name_index=name, kind=kind)
            if kind in (0,6):
                trait["slot"],trait["type"] = reader.u30(),reader.u30()
                trait["value_index"] = reader.u30()
                if trait["value_index"]:
                    trait["vkind"] = reader.u8()
            elif kind in (1,2,3):
                trait["disp"],trait["method"] = reader.u30(),reader.u30()
            elif kind == 4:
                trait["slot"],trait["class"] = reader.u30(),reader.u30()
            elif kind == 5:
                trait["slot"],trait["method"] = reader.u30(),reader.u30()
            else:
                raise ValueError(kind)
            if flags & 64:
                trait["metadata"] = [reader.u30() for _ in range(reader.u30())]
            entries.append(trait)
        return entries

    def parse_tail(self, pos):
        reader = Reader(self.b,pos)
        instances = []
        for _ in range(reader.u30()):
            name,sup = reader.u30(),reader.u30()
            flags = reader.u8()
            protected = reader.u30() if flags & 8 else None
            interfaces = [reader.u30() for _ in range(reader.u30())]
            iinit = reader.u30()
            instances.append(dict(name=self.mn(name),name_index=name,super=self.mn(sup),
                                  super_index=sup,flags=flags,protected_namespace=protected,
                                  interfaces=interfaces,iinit=iinit,inst_traits=self._traits(reader)))
        classes = []
        for instance in instances:
            classes.append(dict(instance,cinit=reader.u30(),traits=self._traits(reader)))
        scripts = []
        for _ in range(reader.u30()):
            scripts.append(dict(init=reader.u30(),traits=self._traits(reader)))
        bodies = []
        for _ in range(reader.u30()):
            method,max_stack,locals_,initial_scope,max_scope = (reader.u30() for _ in range(5))
            code = reader.raw(reader.u30())
            exceptions = [tuple(reader.u30() for _ in range(5)) for _ in range(reader.u30())]
            bodies.append(dict(method=method,code=code,local_count=locals_,max_stack=max_stack,
                               initial_scope=initial_scope,max_scope=max_scope,exceptions=exceptions,
                               traits=self._traits(reader)))
        if reader.pos == len(self.b):
            self.scripts = scripts
        return classes,bodies,reader.pos

    def namespace(self,index):
        if index == 0:
            # Existing E4X getdescendants operands use namespace index zero.
            return (0, "*")
        kind,uri_index = self.namespaces[index]
        uri = self.s(uri_index)
        return (kind,index if kind == 5 else uri)

    def namespace_description(self,index):
        if index == 0:
            return dict(index=0,kind=0,uri="*",identity=[0,"*"])
        kind,uri_index = self.namespaces[index]
        return dict(index=index,kind=kind,uri=self.s(uri_index),identity=list(self.namespace(index)))

    def qname(self,index):
        kind,data = self.multinames[index]
        if kind in (7,13):
            namespace,name = data
            return self.namespace(namespace),self.s(name)
        return None

    def qname_description(self,index):
        kind,data = self.multinames[index]
        if kind in (7,13):
            namespace,name = data
            return dict(multiname_index=index,name=self.s(name),namespace=self.namespace_description(namespace))
        return dict(multiname_index=index,name=self.mn(index),kind=kind)

    def name_and_namespace_candidates(self,index):
        kind,data = self.multinames[index]
        if kind in (7,13):
            namespace,name = data
            return self.s(name),[self.namespace(namespace)]
        if kind in (9,14):
            name,namespace_set = data
            return self.s(name),[self.namespace(namespace) for namespace in self.ns_sets[namespace_set]]
        if kind in (27,28):
            return "<runtime>",[self.namespace(namespace) for namespace in self.ns_sets[data[0]]]
        return "<runtime>",[]


def instructions(code):
    reader = Reader(code,0)
    while reader.pos < len(code):
        pc,opcode = reader.pos,reader.u8()
        name,fmt = OPS.get(opcode,(f"op_{opcode:x}","?"))
        multiname = None
        if fmt in ("m","s","i","u","d"):
            value = reader.u30()
            if fmt == "m":
                multiname = value
        elif fmt == "b":
            reader.u8()
        elif fmt == "j":
            reader.raw(3)
        elif fmt == "CASE":
            reader.raw(3)
            reader.raw(3*(reader.u30()+1))
        elif fmt == "DEBUG":
            reader.u8(),reader.u30(),reader.u8(),reader.u30()
        elif fmt == "mu":
            multiname = reader.u30()
            reader.u30()
        elif fmt == "um":
            reader.u30()
            multiname = reader.u30()
        elif fmt == "uu":
            reader.u30(),reader.u30()
        elif fmt not in ("",None):
            raise ValueError((pc,name,fmt))
        yield dict(pc=pc,opcode=name,multiname=multiname)


def load(path):
    return [ABC(blob).load(tries=10) for blob in audit.abc_blobs(str(path))]


def method_body(path,class_name,method_name):
    for abc in load(path):
        for entry in abc.classes:
            if entry["name"] == class_name:
                for trait in entry["traits"]+entry["inst_traits"]:
                    if "method" in trait and trait["name"].rsplit("::",1)[-1]==method_name:
                        return abc,abc.body_of_method[trait["method"]]
    raise LookupError((class_name,method_name))


def interface_property_contract(path):
    abc,body = method_body(path,"asgui.managers.tooltips::BottomToolTipEffect","UpdatePosition")
    expected_namespace = (8,"asgui.core:IAsguiDisplayObject")
    member_counts = Counter()
    for instruction in instructions(body["code"]):
        if instruction["opcode"] not in ("getproperty","setproperty") or not instruction["multiname"]:
            continue
        name,namespaces = abc.name_and_namespace_candidates(instruction["multiname"])
        if name in ("width","height","x","y") and expected_namespace in namespaces:
            member_counts[(instruction["opcode"],name)] += 1
    # Getter/setter declarations are separate traits, even when they share QName.
    interface = next(entry for entry in abc.classes if entry["name"]=="asgui.core::IAsguiDisplayObject")
    for (opcode,name),count in member_counts.items():
        required_kind = 2 if opcode == "getproperty" else 3
        assert any(trait["kind"]==required_kind and
                   abc.qname(trait["name_index"]) == (expected_namespace,name)
                   for trait in interface["inst_traits"]),(opcode,name,required_kind)
    return member_counts


def global_isnan_contract(path):
    abc,body = method_body(path,"asgui.utils::StarlingUtil","TakeScreenshot")
    refs = []
    for instruction in instructions(body["code"]):
        if instruction["multiname"]:
            name,namespaces = abc.name_and_namespace_candidates(instruction["multiname"])
            if name=="isNaN":
                refs.append((instruction["opcode"],tuple(namespaces)))
    return refs


def audit_file(path):
    problems,protected_problems,counts,private_namespaces = [],[],defaultdict(int),[]
    for abc_id,abc in enumerate(load(path)):
        definitions = defaultdict(list)
        owners = defaultdict(list)
        for entry in abc.classes:
            for key in ("traits","inst_traits"):
                for trait in entry[key]:
                    qname = abc.qname(trait["name_index"])
                    if qname is not None:
                        definitions[qname].append(entry["name"]+"."+key)
                    if "method" in trait:
                        owners[trait["method"]].append(entry["name"]+"."+trait["name"])
            owners[entry["iinit"]].append(entry["name"]+".<iinit>")
            owners[entry["cinit"]].append(entry["name"]+".<cinit>")
        for script in abc.scripts:
            for trait in script["traits"]:
                qname = abc.qname(trait["name_index"])
                if qname is not None:
                    definitions[qname].append("<script>")
        for body in abc.bodies:
            for trait in body["traits"]:
                qname = abc.qname(trait["name_index"])
                if qname is not None:
                    definitions[qname].append("<activation>method"+str(body["method"]))
        counts["definitions"] += len(definitions)
        private_namespaces.extend(dict(abc=abc_id,**abc.namespace_description(index))
                                  for index,value in enumerate(abc.namespaces) if value and value[0] == 5)
        for body in abc.bodies:
            counts["methods"] += 1
            for instruction in instructions(body["code"]):
                index = instruction["multiname"]
                if not index:
                    continue
                qname = abc.qname(index)
                if qname is None:
                    continue
                namespace,name = qname
                counts["qualified_references"] += 1
                if namespace[0] not in (5,24,26):
                    continue
                counts["private_references" if namespace[0] == 5 else "protected_references"] += 1
                if qname not in definitions:
                    matches = [dict(identity=list(ns),owners=targets) for (ns,text),targets in definitions.items() if text == name]
                    target = problems if namespace[0] == 5 else protected_problems
                    target.append(dict(abc=abc_id,method=body["method"],owners=owners[body["method"]],
                                       **instruction,reference=abc.qname_description(index),
                                       same_short_name_definitions=matches))
    return dict(path=str(Path(path).resolve()),counts=dict(counts),unresolved_private_qnames=problems,
                unresolved_protected_qnames=protected_problems,private_namespaces=private_namespaces)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("swf")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = audit_file(args.swf)
    if args.output:
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(path=result["path"],counts=result["counts"],
                          unresolved=len(result["unresolved_private_qnames"]),
                          unresolved_protected=len(result["unresolved_protected_qnames"]),
                          first_errors=result["unresolved_private_qnames"][:5]),ensure_ascii=False,indent=2))
    raise SystemExit(bool(result["unresolved_private_qnames"] or result["unresolved_protected_qnames"]))


if __name__ == "__main__":
    main()
