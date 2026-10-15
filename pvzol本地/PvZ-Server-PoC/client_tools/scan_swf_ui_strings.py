"""Scan live SWF modules for hardcoded Chinese UI strings.

Run from any directory:
    python client_tools/scan_swf_ui_strings.py
    python client_tools/scan_swf_ui_strings.py --all

The default scans the battle/tooltip modules targeted by translate_battle_ui.py.
--all scans top-level Module*.swf files. This does not read or modify clientDB.
"""
from __future__ import annotations

import argparse
import re
import sys
import zlib
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
WEBROOT = SERVER.parent / "webroot"
PATCH_HELPERS = SERVER / "client_patches" / "card-exp-bean-repeat-20260926"
if str(PATCH_HELPERS) not in sys.path:
    sys.path.insert(0, str(PATCH_HELPERS))

from patch_module_card_clickthrough import parse_abc
from translate_battle_ui import MODULE_NAMES, _iter_abc_tags

CJK_RE = re.compile(r"[\u3400-\u9fff]")
TRANSLATION_TERMS = (
    "基础战力", "总战力", "植物拼图", "威望进化", "队长技能",
    "普通攻击", "营养室", "未领取", "已领取", "稀有度", "世界树",
    "实验室", "普攻", "健康", "生命", "战力", "先攻", "总和",
    "果实", "基因", "装备", "洗炼", "装甲", "装扮", "技能",
    "部队总先攻", "上阵植物", "返回小镇", "查看星性",
)


def _structural_indexes(info) -> set[int]:
    protected: set[int] = set()
    for _kind, name_index in info.namespaces:
        protected.add(name_index)
    for item in info.multinames:
        if not item:
            continue
        if item[0] in (0x07, 0x0D):
            protected.add(item[2])
        elif item[0] in (0x09, 0x0E):
            protected.add(item[1])
    return protected


def _raw_swf(path: Path) -> bytes:
    packed = path.read_bytes()
    if packed[:3] == b"CWS":
        raw = packed[:8] + zlib.decompress(packed[8:])
    elif packed[:3] == b"FWS":
        raw = packed
    else:
        raise ValueError("not an FWS/CWS SWF")
    if len(raw) != int.from_bytes(raw[4:8], "little"):
        raise ValueError("incorrect SWF file length")
    return raw


def scan(path: Path) -> tuple[int, list[tuple[str, int, str]]]:
    raw = _raw_swf(path)
    blocks = list(_iter_abc_tags(raw))
    matches: list[tuple[str, int, str]] = []
    for block_no, (start, end, _tag_start, _header_len, _tag_len) in enumerate(blocks, 1):
        info = parse_abc(raw[start:end])
        protected = _structural_indexes(info)
        for index, value in enumerate(info.strings):
            if not value or not CJK_RE.search(value):
                continue
            relevant = any(term in value for term in TRANSLATION_TERMS)
            is_structural = index in protected
            # Show relevant display strings and unclassified Chinese strings in
            # targeted modules; mark identifiers so they aren't blindly replaced.
            if relevant or not is_structural:
                matches.append((value, block_no, "IDENTIFIER" if is_structural else "DISPLAY"))
    return len(blocks), matches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="scan every top-level Module*.swf")
    args = parser.parse_args()

    if args.all:
        paths = sorted(WEBROOT.glob("Module*.swf"))
    else:
        paths = [WEBROOT / name for name in MODULE_NAMES]
    existing = [path for path in paths if path.is_file()]
    if not existing:
        print("No target SWFs found in %s" % WEBROOT)
        return 1

    total = 0
    for path in existing:
        try:
            blocks, matches = scan(path)
        except Exception as exc:
            print("%s: ERROR: %s" % (path.name, exc))
            continue
        if not matches:
            print("%s: %d ABC block(s), no Chinese strings matched for review" % (path.name, blocks))
            continue
        print("\n%s: %d ABC block(s), %d Chinese strings" % (path.name, blocks, len(matches)))
        seen: set[tuple[str, str]] = set()
        for value, block_no, kind in matches:
            key = (value, kind)
            if key in seen:
                continue
            seen.add(key)
            print("  [%s; ABC %d] %s" % (kind, block_no, value.replace("\n", "\\n")))
            total += 1
    print("\nDistinct Chinese strings printed: %d" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
