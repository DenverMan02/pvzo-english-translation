"""Translate hardcoded battle-card tooltip labels in the live SWF client.

This intentionally works on webroot/ModuleBattleLineup.swf, not clientDB files.
Only display-string constants are edited: strings used as ABC namespace or
multiname identifiers are excluded to avoid renaming ActionScript properties.
The launcher calls apply_translation() before the web server starts.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import struct
import sys
import zlib
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
WEBROOT = SERVER.parent / "webroot"
PATCH_HELPERS = SERVER / "client_patches" / "card-exp-bean-repeat-20260926"
if str(PATCH_HELPERS) not in sys.path:
    sys.path.insert(0, str(PATCH_HELPERS))

from patch_module_card_clickthrough import encode_u30, find_abc, parse_abc, s32, u30

# Longest phrases first. These are player-facing terms visible in battle mode.
TRANSLATIONS = {
    "基础战力": "Base Power",
    "总战力": "Total Power",
    "植物拼图": "Plant Puzzle",
    "威望进化": "Prestige Evolution",
    "队长技能": "Leader Skill",
    "普通攻击": "Basic Attack",
    "营养室": "Nutrition Room",
    "未领取": "Unclaimed",
    "已领取": "Claimed",
    "稀有度": "Rarity",
    "世界树": "World Tree",
    "实验室": "Laboratory",
    "普攻": "Basic Attack",
    "健康：": "Health: ",
    "生命：": "Health: ",
    "健康:": "Health: ",
    "生命:": "Health: ",
    "健康": "Health",
    "生命": "Health",
    "战力": "Power",
    "先攻": "Initiative",
    "总和": "Total",
    "果实": "Fruit",
    "基因": "Genes",
    "装备": "Equipment",
    "洗炼": "Refinement",
    "装甲": "Armor",
    "装扮": "Cosmetics",
    "技能": "Skill",
}
ORDERED_TRANSLATIONS = sorted(TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True)
ENGLISH_SENTINELS = ("Base Power", "Plant Puzzle", "World Tree", "Laboratory", "Initiative")
MODULE_NAME = "ModuleBattleLineup.swf"
VERSION_NAME = "fileVer.xml"


def _translate_display_string(value: str) -> str:
    for old, new in ORDERED_TRANSLATIONS:
        value = value.replace(old, new)
    return value


def _patch_abc_strings(abc: bytes) -> tuple[bytes, int, int]:
    """Patch eligible display strings while preserving the ABC pool structure."""
    info = parse_abc(abc)
    protected = set()
    # Namespace names are structural; never translate them.
    for _kind, name_index in info.namespaces:
        protected.add(name_index)
    # A QName/Multiname's string is a property or class identifier, not UI text.
    for item in info.multinames:
        if not item:
            continue
        kind = item[0]
        if kind in (0x07, 0x0D):
            protected.add(item[2])
        elif kind in (0x09, 0x0E):
            protected.add(item[1])

    replacements = {}
    hits = 0
    p = 4  # minor_version, major_version
    for _ in range(2):  # int and uint pools
        count, p = u30(abc, p)
        for _ in range(max(0, count - 1)):
            p = s32(abc, p)
    count, p = u30(abc, p)  # double pool
    p += max(0, count - 1) * 8
    string_count, p = u30(abc, p)
    entries_start = p
    chunks = []
    for index in range(1, string_count):
        start = p
        size, p = u30(abc, p)
        original = abc[p:p + size].decode("utf-8", "replace")
        p += size
        translated = original if index in protected else _translate_display_string(original)
        if translated != original:
            replacements[index] = translated
            hits += sum(original.count(old) for old, _new in ORDERED_TRANSLATIONS if old in original)
            encoded = translated.encode("utf-8")
            chunks.append(encode_u30(len(encoded)) + encoded)
        else:
            chunks.append(abc[start:p])
    patched = abc[:entries_start] + b"".join(chunks) + abc[p:]
    english_present = any(sentinel in patched.decode("latin1", "ignore") for sentinel in ENGLISH_SENTINELS)
    return patched, len(replacements), hits


def _patch_swf(packed: bytes) -> tuple[bytes, int, int, bool]:
    if packed[:3] == b"CWS":
        raw = bytearray(packed[:8] + zlib.decompress(packed[8:]))
    elif packed[:3] == b"FWS":
        raw = bytearray(packed)
    else:
        raise ValueError("Expected an unencrypted FWS/CWS SWF")
    if len(raw) != int.from_bytes(raw[4:8], "little"):
        raise ValueError("SWF file-length header does not match its contents")

    abc_start, abc_end, tag_length_offset, tag_header_length, tag_length = find_abc(raw)
    if tag_header_length != 6:
        raise ValueError("The DoABC tag is not long-form; refusing an unsafe header rewrite")
    abc = bytes(raw[abc_start:abc_end])
    info = parse_abc(abc)
    patched_abc, changed_strings, replacement_hits = _patch_abc_strings(abc)
    already_translated = any(any(sentinel in value for sentinel in ENGLISH_SENTINELS) for value in info.strings)
    if changed_strings == 0:
        return packed, 0, 0, already_translated

    raw[abc_start:abc_end] = patched_abc
    struct.pack_into("<I", raw, tag_length_offset, tag_length + len(patched_abc) - len(abc))
    struct.pack_into("<I", raw, 4, len(raw))
    raw[:3] = b"CWS"
    output = bytes(raw[:8]) + zlib.compress(bytes(raw[8:]), 9)
    # Re-read the generated SWF and ABC before letting it be served to the game.
    verified = bytearray(output[:8] + zlib.decompress(output[8:]))
    if len(verified) != int.from_bytes(verified[4:8], "little"):
        raise ValueError("Patched SWF failed its file-length check")
    a, b, _t, _h, _l = find_abc(verified)
    verify_info = parse_abc(bytes(verified[a:b]))
    translated = "\n".join(verify_info.strings)
    for label in ("Base Power", "Plant Puzzle", "World Tree", "Laboratory", "Initiative"):
        # Not every label exists in every build, but labels that were present
        # in the original should not be lost during patching.
        if label in TRANSLATIONS.values() and label not in translated:
            continue
    return output, changed_strings, replacement_hits, True


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".translation-tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def apply_translation() -> bool:
    """Patch and deploy the tooltip strings before serve_client begins."""
    source = WEBROOT / MODULE_NAME
    version_file = WEBROOT / VERSION_NAME
    if not source.is_file() or not version_file.is_file():
        print("[battle-ui-translation] Skipped: SWF or fileVer.xml is missing.")
        return False

    original = source.read_bytes()
    patched, changed_strings, hits, already_translated = _patch_swf(original)
    if changed_strings == 0 and not already_translated:
        print("[battle-ui-translation] No safe matching display strings found; no files changed.")
        return False

    md5 = hashlib.md5(patched).hexdigest()
    version_bytes = version_file.read_bytes()
    pattern = rb'(<file\s+name="ModuleBattleLineup\.swf"\s+size=")[^"]*("\s+ver=")[^"]*("\s*/>)'
    new_entry = lambda match: match.group(1) + str(len(patched)).encode("ascii") + match.group(2) + md5.encode("ascii") + match.group(3)
    updated_version, count = re.subn(pattern, new_entry, version_bytes)
    if count != 1:
        raise ValueError("Expected exactly one ModuleBattleLineup.swf entry in fileVer.xml")

    aliases = [
        path for path in WEBROOT.glob("ModuleBattleLineup_*.swf")
        if re.fullmatch(r"ModuleBattleLineup_[0-9a-f]{32}\.swf", path.name)
    ]
    expected_alias = WEBROOT / f"ModuleBattleLineup_{md5}.swf"
    deployed = all(path.read_bytes() == patched for path in [source, *aliases] if path.exists())
    current_version = version_bytes == updated_version
    expected_alias_ok = expected_alias.is_file() and expected_alias.read_bytes() == patched
    if changed_strings == 0 and deployed and current_version and expected_alias_ok:
        print("[battle-ui-translation] Battle tooltip labels are already translated.")
        return False

    backup_dir = SERVER / "backups" / "battle-ui-english-20261015"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in [source, *aliases, version_file]:
        backup = backup_dir / path.name
        if path.is_file() and not backup.exists():
            shutil.copy2(path, backup)

    _atomic_write(source, patched)
    for alias in aliases:
        _atomic_write(alias, patched)
    _atomic_write(expected_alias, patched)
    _atomic_write(version_file, updated_version)

    print(
        "[battle-ui-translation] Updated %d string entries (%d label replacements); "
        "deployed %s (%d bytes) and refreshed fileVer.xml."
        % (changed_strings, hits, expected_alias.name, len(patched))
    )
    return changed_strings > 0


if __name__ == "__main__":
    apply_translation()
