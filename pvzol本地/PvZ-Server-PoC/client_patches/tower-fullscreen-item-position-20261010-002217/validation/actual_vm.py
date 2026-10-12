"""Execute selected, parsed AVM2 method bodies against synthetic native interfaces.

This intentionally is not a Flash renderer. It validates the actual compiled
coordinate/viewport bytecode without interacting with any Windows UI.
"""
from __future__ import annotations

import ast
import importlib.util
import math
import re
import zlib
from pathlib import Path
from types import SimpleNamespace

AUDIT = Path(__file__).resolve().with_name('original_abc_audit.py')
spec = importlib.util.spec_from_file_location("resize_abc_audit", AUDIT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _swf_body(path):
    raw = Path(path).read_bytes()
    return zlib.decompress(raw[8:]) if raw[:3] == b"CWS" else raw[8:]


audit.ns["swf_body"] = _swf_body


class Rectangle(SimpleNamespace):
    def __init__(self, x=0, y=0, width=0, height=0):
        super().__init__(x=x, y=y, width=width, height=height)

    def setTo(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height

    def clone(self):
        return Rectangle(self.x, self.y, self.width, self.height)

    def intersection(self, other):
        x, y = max(self.x, other.x), max(self.y, other.y)
        right = min(self.x + self.width, other.x + other.width)
        bottom = min(self.y + self.height, other.y + other.height)
        return Rectangle(x, y, max(0, right-x), max(0, bottom-y))


class MouseEvent(SimpleNamespace):
    MOUSE_DOWN = "mouseDown"
    MOUSE_UP = "mouseUp"
    MOUSE_MOVE = "mouseMove"


class TouchEvent(SimpleNamespace):
    TOUCH_BEGIN = "touchBegin"
    TOUCH_MOVE = "touchMove"
    TOUCH_END = "touchEnd"


class VM:
    def __init__(self, path):
        self.path = Path(path)
        self.abcs = [audit.Abc(b).load(tries=10) for b in audit.abc_blobs(str(path))]
        self.globals = {
            "::int": int, "::uint": int, "::Number": float, "::Boolean": bool,
            "flash.geom::Rectangle": Rectangle,
            "flash.events::MouseEvent": MouseEvent,
            "flash.events::TouchEvent": TouchEvent,
            "flash.ui::Mouse": SimpleNamespace(supportsCursor=False),
            "starling.events::TouchPhase": SimpleNamespace(
                BEGAN="began", MOVED="moved", ENDED="ended", HOVER="hover"),
            "::Math": SimpleNamespace(min=min, max=max, round=round, floor=math.floor),
        }

    def method(self, class_name, method_name, kind=1):
        for abc in self.abcs:
            for entry in abc.classes:
                if entry["name"] != class_name:
                    continue
                for trait in entry["traits"] + entry["inst_traits"]:
                    if trait.get("kind") == kind and self.short(trait["name"]) == method_name:
                        return abc, abc.body_of_method[trait["method"]]
        raise LookupError((class_name, method_name, kind))

    @staticmethod
    def short(name):
        return name.rsplit("::", 1)[-1]

    def get(self, obj, name):
        key = self.short(name)
        if key == "hasOwnProperty":
            return lambda value: value in obj if isinstance(obj, dict) else hasattr(obj, value)
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            return obj[key]
        if isinstance(obj, (list, tuple)) and key == "length":
            return len(obj)
        return getattr(obj, key)

    def set(self, obj, name, value):
        key = self.short(name)
        if isinstance(obj, dict):
            obj[key] = value
        else:
            setattr(obj, key, value)

    def execute(self, class_name, method_name, this, args=(), kind=1):
        abc, body = self.method(class_name, method_name, kind)
        return self.execute_body(abc, body, this, args, label=(class_name, method_name))

    def execute_body(self, abc, body, this, args=(), captured_scopes=(), label=()):
        instructions = []
        for line in abc.disassemble(body["code"]):
            match = re.match(r"\s*(\d+)\s+(\S+)\s*(.*)", line)
            if not match:
                raise RuntimeError(line)
            instructions.append((int(match[1]), match[2], match[3]))
        positions = {pc: i for i, (pc, _, _) in enumerate(instructions)}
        loc = [this, *args] + [None] * max(32, body.get("local_count", 0))
        stack, scopes = [], list(captured_scopes)
        i, count = 0, 0
        while i < len(instructions):
            pc, op, operand = instructions[i]
            i += 1
            count += 1
            if count > 20000:
                raise RuntimeError("AVM2 instruction limit")
            if op.startswith("getlocal"):
                stack.append(loc[int(op[-1]) if len(op) == 9 else int(operand)])
            elif op.startswith("setlocal"):
                loc[int(op[-1]) if len(op) == 9 else int(operand)] = stack.pop()
            elif op in ("pushbyte", "pushshort", "pushint", "pushuint", "pushdouble"):
                value = float(operand) if op == "pushdouble" else int(operand)
                if op == "pushbyte" and value >= 128:
                    value -= 256
                stack.append(value)
            elif op == "pushnan":
                stack.append(math.nan)
            elif op == "pushnull" or op == "pushundefined":
                stack.append(None)
            elif op in ("pushtrue", "pushfalse"):
                stack.append(op == "pushtrue")
            elif op == "pushstring":
                stack.append(ast.literal_eval(operand))
            elif op == "pushscope":
                scopes.append(stack.pop())
            elif op == "popscope":
                scopes.pop()
            elif op == "newactivation":
                stack.append({"__slot_names": {trait["slot"]:self.short(trait["name"])
                                              for trait in body["traits"] if "slot" in trait}})
            elif op == "getslot":
                stack.append(stack.pop()[int(operand)])
            elif op == "setslot":
                value, obj = stack.pop(), stack.pop()
                slot = int(operand)
                obj[slot] = value
                if slot in obj.get("__slot_names", {}):
                    obj[obj["__slot_names"][slot]] = value
            elif op == "newfunction":
                method_index, capture = int(operand), tuple(scopes)
                def function(*params, method_index=method_index, capture=capture):
                    return self.execute_body(abc, abc.body_of_method[method_index], this, params, capture)
                stack.append(function)
            elif op == "newarray":
                count = int(operand)
                values = stack[-count:] if count else []
                if count:
                    del stack[-count:]
                stack.append(values)
            elif op == "newobject":
                count = int(operand)
                values = stack[-2*count:] if count else []
                if count:
                    del stack[-2*count:]
                stack.append(dict(zip(values[::2], values[1::2])))
            elif op == "getglobalscope":
                stack.append(self.globals)
            elif op == "getscopeobject":
                stack.append(scopes[int(operand)])
            elif op == "getlex":
                if operand in self.globals:
                    stack.append(self.globals[operand])
                else:
                    for scope in reversed(scopes):
                        try:
                            value = self.get(scope, operand)
                        except (AttributeError, KeyError):
                            continue
                        stack.append(value)
                        break
                    else:
                        raise KeyError(operand)
            elif op in ("findpropstrict", "findproperty"):
                if operand in self.globals:
                    stack.append(self.globals)
                elif hasattr(this, self.short(operand)):
                    stack.append(this)
                else:
                    stack.append(self.globals)
            elif op == "getproperty":
                key = stack.pop() if operand == "<runtime>" else operand
                obj = stack.pop()
                stack.append(obj[key] if operand == "<runtime>" and isinstance(obj,(list,tuple,dict))
                             else self.get(obj, key))
            elif op in ("setproperty", "initproperty"):
                value = stack.pop()
                key = stack.pop() if operand == "<runtime>" else operand
                obj = stack.pop()
                if operand == "<runtime>":
                    obj[key] = value
                else:
                    self.set(obj, key, value)
            elif op in ("callproperty", "callpropvoid", "constructprop"):
                name, nargs = operand.rsplit(", ", 1)
                nargs = int(nargs)
                params = stack[-nargs:] if nargs else []
                if nargs:
                    del stack[-nargs:]
                obj = stack.pop()
                value = self.get(obj, name)(*params)
                if op != "callpropvoid":
                    stack.append(value)
            elif op == "callsupervoid":
                name, nargs = operand.rsplit(", ", 1)
                nargs = int(nargs)
                params = stack[-nargs:] if nargs else []
                if nargs:
                    del stack[-nargs:]
                obj = stack.pop()
                self.get(obj, "__super_" + self.short(name))(*params)
            elif op in ("call", "construct"):
                nargs = int(operand)
                params = stack[-nargs:] if nargs else []
                if nargs:
                    del stack[-nargs:]
                if op == "call":
                    stack.pop()  # receiver supplied to an ActionScript function
                function = stack.pop()
                stack.append(function(*params))
            elif op in ("astypelate", "istypelate"):
                cls, value = stack.pop(), stack.pop()
                stack.append(isinstance(value, cls) if op == "istypelate"
                             else (value if isinstance(value, cls) else None))
            elif op in ("coerce", "coerce_a", "coerce_s", "convert_s"):
                pass
            elif op in ("convert_d", "convert_i", "convert_u", "convert_b"):
                value = stack.pop()
                convert = {"convert_d": float, "convert_i": int,
                           "convert_u": int, "convert_b": bool}[op]
                stack.append(convert(value))
            elif op == "dup":
                stack.append(stack[-1])
            elif op == "pop":
                stack.pop()
            elif op == "swap":
                stack[-2:] = stack[-2:][::-1]
            elif op in ("add", "subtract", "multiply", "divide", "modulo",
                        "equals", "strictequals", "lessthan", "greaterthan", "lessequals", "greaterequals"):
                b, a = stack.pop(), stack.pop()
                stack.append({"add": lambda: str(a)+str(b) if isinstance(a,str) or isinstance(b,str) else a+b, "subtract": lambda: a-b,
                              "multiply": lambda: a*b, "divide": lambda: a/b,
                              "modulo": lambda: a%b, "equals": lambda: a == b,
                              "strictequals": lambda: a == b, "lessthan": lambda: a < b,
                              "greaterthan": lambda: a > b, "lessequals": lambda: a <= b,
                              "greaterequals": lambda: a >= b}[op]())
            elif op == "negate":
                stack.append(-stack.pop())
            elif op in ("increment", "decrement", "increment_i", "decrement_i"):
                stack.append(stack.pop() + (-1 if op.startswith("decrement") else 1))
            elif op == "not":
                stack.append(not stack.pop())
            elif op == "in":
                obj, key = stack.pop(), stack.pop()
                stack.append(key in obj if isinstance(obj, dict) else hasattr(obj, key))
            elif op in ("kill", "inclocal", "inclocal_i", "declocal", "declocal_i"):
                n = int(operand)
                loc[n] = None if op == "kill" else loc[n] + (-1 if op.startswith("dec") else 1)
            elif op in ("iftrue", "iffalse", "jump", "ifeq", "ifne", "iflt", "ifle",
                        "ifgt", "ifge", "ifnlt", "ifnle", "ifngt", "ifnge", "ifstricteq", "ifstrictne"):
                if op == "jump":
                    condition = True
                elif op in ("iftrue", "iffalse"):
                    condition = bool(stack.pop()) == (op == "iftrue")
                else:
                    b, a = stack.pop(), stack.pop()
                    condition = {"ifeq": lambda: a == b, "ifne": lambda: a != b,
                                 "ifstricteq": lambda: a == b, "ifstrictne": lambda: a != b,
                                 "iflt": lambda: a < b, "ifle": lambda: a <= b,
                                 "ifgt": lambda: a > b, "ifge": lambda: a >= b,
                                 "ifnlt": lambda: not(a < b), "ifnle": lambda: not(a <= b),
                                 "ifngt": lambda: not(a > b), "ifnge": lambda: not(a >= b)}[op]()
                if condition:
                    i = positions[int(operand.split("-> ")[-1])]
            elif op == "lookupswitch":
                index = stack.pop()
                default, cases = operand.split("; cases ")
                targets = ast.literal_eval(cases)
                target = targets[index] if 0 <= index < len(targets) else int(default.split("-> ")[-1])
                i = positions[target]
            elif op in ("label", "nop", "debug", "debugline", "debugfile"):
                pass
            elif op == "returnvoid":
                assert not stack, (label, pc, stack)
                return None
            elif op == "returnvalue":
                return stack.pop()
            elif op == "throw":
                raise stack.pop()
            else:
                raise NotImplementedError((self.path.name, label, pc, op, operand))
        raise RuntimeError("Method fell through without return")


def mock_starling(native_width, native_height, logical_width=1440, logical_height=900):
    backbuffers, touches = [], []
    obj = SimpleNamespace(
        mStage=SimpleNamespace(stageWidth=logical_width, stageHeight=logical_height),
        mNativeStage=SimpleNamespace(stageWidth=native_width, stageHeight=native_height),
        mViewPort=Rectangle(0, 0, logical_width, logical_height),
        mPreviousViewPort=Rectangle(0, 0, 0, 0),
        mStage3D=SimpleNamespace(x=0, y=0),
        mNativeOverlay=SimpleNamespace(x=0, y=0, scaleX=1, scaleY=1),
        mShareContext=False, mProfile="baseline", mAntiAliasing=0,
        mSupportHighResolutions=False, mStarted=True, mLeftMouseDown=False,
        mTouchProcessor=SimpleNamespace(enqueue=lambda *a: touches.append(a)),
        configureBackBuffer=lambda *a: backbuffers.append(a),
    )
    return obj, backbuffers, touches

