#!/usr/bin/env python3
"""
PVZ Online clientDB.txt Translation Tool

clientDB.txt is a binary container of UTF-8 files:
    u32 BE filename length
    filename bytes
    u32 BE data length
    data bytes
    repeat...

This tool makes translation easier by keeping the original embedded bytes in
original/ and creating a much smaller translation workspace in translate/.
For TSV database files, translate/ contains only the ID column plus likely
human-facing text columns. Repacking merges those translations back into the
original TSV, so every internal/numeric field comes from original/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

DEFAULT_CHUNK_BYTES = 900_000
MAX_FILENAME_BYTES = 4096

KNOWN_TRANSLATION_COLUMNS = {
    "name", "desc", "description", "introduction", "title", "text",
    "content", "dialog", "dialogue", "talk", "tips", "tip", "help",
    "message", "label", "remark", "story"
}

NEVER_TRANSLATE = {
    "id", "aimode", "unlockindex", "resname", "framerate", "inithidebones",
    "actionlist", "specialactionname", "specialskillparam", "bottombone",
    "staticactionname", "fireposinfo", "params", "isattackflight",
    "isattackobj", "attacktype", "damagevalue", "damagevalueforshielded",
    "specialskilldamagevalue", "specialskilldamagevalueforshielded",
    "duration", "bullettype", "hp", "price", "cardrefreshduration", "bgid",
    "iconid", "icon2id", "usesunornight", "sortindex", "series", "level",
    "usefulness", "bodyheight", "roadtilestate", "forbidtilestate",
    "preplanttype", "damagetype", "bombeffectid", "catalog", "isblit",
    "initbuffliststr", "iseaplant", "needshade", "itemresidlist",
    "cardhidebone", "forgecostratio", "endlesstdscoreratio", "costwater",
    "costticket", "costdiamond"
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def u32be(data: bytes, pos: int) -> int:
    if pos + 4 > len(data):
        raise ValueError(f"Unexpected EOF at 0x{pos:X}")
    return struct.unpack_from(">I", data, pos)[0]


def p32be(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"Length out of u32 range: {value}")
    return struct.pack(">I", value)


def normalize_header(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.strip().lower())


def cjk_count(s: str) -> int:
    return sum(("\u3400" <= c <= "\u4DBF") or ("\u4E00" <= c <= "\u9FFF") for c in s)


def safe_name(name: str, index: int, suffix: str = "") -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = f"entry_{index:04d}"
    return f"{index:04d}_{name}{suffix}"


def is_tsv(text: str) -> bool:
    lines = text.splitlines()
    if not lines:
        return False
    sample = lines[:min(20, len(lines))]
    return sum("\t" in x for x in sample) >= max(1, len(sample) // 2)


def choose_translation_columns(text: str) -> tuple[list[str], list[int]]:
    lines = text.splitlines()
    if not lines:
        return [], []
    headers = lines[0].split("\t")
    rows = [x.split("\t") for x in lines[1:501]]
    norm = [normalize_header(x) for x in headers]
    selected: set[int] = set()

    # Keep a stable row key in every translation file.
    selected.add(norm.index("id") if "id" in norm else 0)

    for i, h in enumerate(norm):
        if h in NEVER_TRANSLATE:
            continue
        if h in KNOWN_TRANSLATION_COLUMNS:
            selected.add(i)

    # Catch human-facing Chinese columns whose header name is unusual.
    for i, h in enumerate(norm):
        if i in selected or h in NEVER_TRANSLATE:
            continue
        chinese_rows = 0
        nonempty = 0
        for row in rows:
            if i >= len(row) or not row[i].strip():
                continue
            nonempty += 1
            if cjk_count(row[i]) >= 2:
                chinese_rows += 1
        if chinese_rows >= 2 or (chinese_rows == 1 and nonempty <= 3):
            selected.add(i)

    indices = sorted(selected)
    return [headers[i] for i in indices], indices


def parse_container(raw: bytes) -> list[dict]:
    entries = []
    pos = 0
    idx = 0
    while pos < len(raw):
        record_start = pos
        name_len = u32be(raw, pos)
        pos += 4
        if not 1 <= name_len <= MAX_FILENAME_BYTES:
            raise ValueError(
                f"Invalid filename length {name_len} at 0x{record_start:X}."
            )
        name_bytes = raw[pos:pos + name_len]
        if len(name_bytes) != name_len:
            raise ValueError("Filename extends past EOF.")
        pos += name_len
        try:
            name = name_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(f"Entry {idx} filename is not UTF-8.") from e
        if pos + 4 > len(raw):
            raise ValueError(f"Missing data length after {name!r}.")
        data_len = u32be(raw, pos)
        pos += 4
        data = raw[pos:pos + data_len]
        if len(data) != data_len:
            raise ValueError(
                f"{name!r} claims {data_len} bytes but only {len(data)} remain."
            )
        pos += data_len
        entries.append({
            "index": idx,
            "name": name,
            "name_bytes": name_bytes,
            "data": data,
            "length": data_len,
            "record_start": record_start,
        })
        idx += 1
    return entries


def split_lines_to_parts(text: str, directory: Path, stem: str, limit: int) -> list[str]:
    raw_size = len(text.encode("utf-8"))
    if raw_size <= limit:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    lines = text.splitlines(keepends=True)
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        n = len(line.encode("utf-8"))
        if current and size + n > limit:
            part_name = f"{stem}.part{len(parts)+1:04d}.txt"
            (directory / part_name).write_text("".join(current), encoding="utf-8", newline="")
            parts.append(part_name)
            current = []
            size = 0
        current.append(line)
        size += n
    if current:
        part_name = f"{stem}.part{len(parts)+1:04d}.txt"
        (directory / part_name).write_text("".join(current), encoding="utf-8", newline="")
        parts.append(part_name)
    return parts


def make_translation_file(text: str, headers: list[str], indices: list[int]) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    out = ["\t".join(headers)]
    for line in lines[1:]:
        row = line.split("\t")
        out.append("\t".join(row[i] if i < len(row) else "" for i in indices))
    return "\n".join(out) + "\n"


def extract(source: Path, dest: Path, chunk_limit: int) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists. Delete it or choose another folder.")

    entries = parse_container(source.read_bytes())
    for folder in ("original", "text", "translate", "chunks"):
        (dest / folder).mkdir(parents=True)

    manifest_entries = []
    guide_lines = [
        "PVZOL TRANSLATION MAP",
        "=====================",
        "",
        "Edit ONLY files in translate\\.",
        "Keep every ID unchanged and keep the tabs.",
        "",
    ]

    for e in entries:
        idx, name, data = e["index"], e["name"], e["data"]
        try:
            text = data.decode("utf-8")
            text_ok = True
        except UnicodeDecodeError:
            text = None
            text_ok = False

        disk = safe_name(name, idx, "" if text_ok else ".bin")
        original_rel = Path("original") / f"{disk}.bin"
        (dest / original_rel).write_bytes(data)

        text_rel = None
        translation_rel = None
        translation_columns: list[str] = []
        parts: list[str] = []
        tsv = False

        if text_ok:
            text_rel = Path("text") / disk
            (dest / text_rel).write_text(text, encoding="utf-8", newline="")

            if is_tsv(text):
                tsv = True
                translation_columns, indices = choose_translation_columns(text)
                translation_content = make_translation_file(text, translation_columns, indices)
                translation_rel = Path("translate") / disk
                (dest / translation_rel).write_text(
                    translation_content, encoding="utf-8", newline=""
                )
                parts = split_lines_to_parts(
                    translation_content,
                    dest / "chunks" / Path(disk).stem,
                    Path(disk).stem,
                    chunk_limit,
                )
                guide_lines.append(
                    f"{name} -> {', '.join(translation_columns)}"
                )
            else:
                translation_rel = Path("translate") / disk
                (dest / translation_rel).write_text(text, encoding="utf-8", newline="")
                parts = split_lines_to_parts(
                    text, dest / "chunks" / Path(disk).stem,
                    Path(disk).stem, chunk_limit
                )
                guide_lines.append(f"{name} -> full text")
        else:
            guide_lines.append(f"{name} -> binary/uneditable")

        manifest_entries.append({
            "index": idx,
            "name": name,
            "name_hex": e["name_bytes"].hex(),
            "original_length": e["length"],
            "text": text_ok,
            "tsv": tsv,
            "translation_columns": translation_columns,
            "original_file": str(original_rel),
            "text_file": str(text_rel) if text_rel else None,
            "translation_file": str(translation_rel) if translation_rel else None,
            "chunk_files": [
                str(Path("chunks") / Path(disk).stem / x) for x in parts
            ],
        })

    manifest = {
        "tool_version": "4.0",
        "format": {
            "record": "u32 BE filename length + UTF-8 filename + u32 BE data length + data",
            "text": "UTF-8",
        },
        "source": {
            "name": source.name,
            "size": source.stat().st_size,
            "sha256": sha256_file(source),
        },
        "chunk_limit_bytes": chunk_limit,
        "entry_count": len(entries),
        "entries": manifest_entries,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    readme = f"""PVZ ONLINE CLIENTDB TRANSLATION WORKSPACE
========================================

EDIT:
    translate\\

SOURCE OF TRUTH:
    original\\

REFERENCE ONLY:
    text\\
    chunks\\

For TSV files, translate\\ contains only the ID plus likely human-facing
text columns. The repacker takes every other field directly from original\\.

Rules:
    - DO NOT change IDs.
    - DO NOT add, delete, or reorder rows.
    - DO NOT change tabs into spaces.
    - You MAY make translated text longer or shorter.
    - Do NOT edit original\\ or manifest.json.

The chunks\\ folder exists so huge translation files can be opened in pieces.
The authoritative translation file remains translate\\<filename>.txt.

Entries: {len(entries):,}
Text entries: {sum(e['text'] for e in manifest_entries):,}
TSV entries: {sum(e['tsv'] for e in manifest_entries):,}
"""
    (dest / "README.txt").write_text(readme, encoding="utf-8")
    (dest / "TRANSLATION_MAP.txt").write_text("\n".join(guide_lines) + "\n", encoding="utf-8")

    print("Extraction complete.")
    print(f"  Entries: {len(entries):,}")
    print(f"  Text: {sum(e['text'] for e in manifest_entries):,}")
    print(f"  TSV: {sum(e['tsv'] for e in manifest_entries):,}")
    print(f"  Edit here: {dest / 'translate'}")


def merge_tsv(original: str, translation: str) -> str:
    """Merge translation columns back by ROW POSITION.

    IDs are kept as a safety check, but are NOT used as the database key.
    PVZOL has tables where IDs can legitimately repeat, so using a dict keyed
    by id is unsafe. The translation workspace is generated in the same row
    order as the original, so row-position matching is deterministic.
    """
    orig_lines = original.splitlines(keepends=False)
    trans_lines = translation.splitlines(keepends=False)
    if not orig_lines or not trans_lines:
        raise ValueError("TSV is empty.")

    oh = orig_lines[0].split("\t")
    th = trans_lines[0].split("\t")
    on = [normalize_header(x) for x in oh]

    targets: list[int] = []
    for h in th:
        matches = [
            i for i, x in enumerate(oh)
            if normalize_header(x) == normalize_header(h)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Translation column {h!r} is not uniquely present in original TSV."
            )
        targets.append(matches[0])

    rows = [x.split("\t") for x in orig_lines]
    trans_rows = [x.split("\t") for x in trans_lines[1:]]

    if len(trans_rows) != len(rows) - 1:
        raise ValueError(
            "Translation row count differs from original. "
            "Do not add, delete, or reorder rows."
        )

    oid = on.index("id") if "id" in on else None
    tid = [normalize_header(x) for x in th].index("id") if "id" in [normalize_header(x) for x in th] else None

    for rno, trow in enumerate(trans_rows, 1):
        if len(trow) != len(targets):
            raise ValueError(
                f"Translation row {rno} has {len(trow)} columns; "
                f"expected {len(targets)}."
            )

        row = rows[rno]
        if len(row) < len(oh):
            row.extend([""] * (len(oh) - len(row)))

        # If both sides have an ID, verify that the translator did not
        # accidentally reorder/change IDs. Duplicate IDs are allowed.
        if oid is not None and tid is not None:
            original_id = row[oid] if oid < len(row) else ""
            translated_id = trow[tid] if tid < len(trow) else ""
            if original_id != translated_id:
                raise ValueError(
                    f"ID changed/reordered at row {rno}: "
                    f"original={original_id!r}, translation={translated_id!r}"
                )

        for ti, oi in enumerate(targets):
            row[oi] = trow[ti]

    return "\n".join("\t".join(row) for row in rows) + "\n"

def rebuild_entry(root: Path, e: dict) -> bytes:
    original = (root / e["original_file"]).read_bytes()
    translation = e.get("translation_file")
    if not e["text"] or not translation:
        return original
    tp = root / translation
    if not tp.is_file():
        return original

    edited = tp.read_text(encoding="utf-8", newline="")
    original_text = original.decode("utf-8")
    if e["tsv"]:
        return merge_tsv(original_text, edited).encode("utf-8")
    return edited.encode("utf-8")


def verify_bytes(raw: bytes, expected: int | None = None) -> list[dict]:
    entries = parse_container(raw)
    if expected is not None and len(entries) != expected:
        raise ValueError(f"Expected {expected} entries, got {len(entries)}.")
    return entries


def repack(root: Path, output: Path, force: bool = False) -> None:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["entries"]
    if output.exists() and not force:
        raise FileExistsError(f"{output} exists. Choose another output or use --force.")

    temp = output.with_name(output.name + ".tmp")
    changed = 0
    try:
        with temp.open("wb") as out:
            for e in entries:
                name_bytes = bytes.fromhex(e["name_hex"])
                original = (root / e["original_file"]).read_bytes()
                data = rebuild_entry(root, e)
                if data != original:
                    changed += 1
                out.write(p32be(len(name_bytes)))
                out.write(name_bytes)
                out.write(p32be(len(data)))
                out.write(data)

        verify_bytes(temp.read_bytes(), len(entries))
        temp.replace(output)
    finally:
        if temp.exists():
            temp.unlink()

    print("Repack complete and verified.")
    print(f"  Entries: {len(entries):,}")
    print(f"  Changed entries: {changed:,}")
    print(f"  Size: {output.stat().st_size:,} bytes")
    print(f"  SHA-256: {sha256_file(output)}")


def verify(path: Path) -> None:
    raw = path.read_bytes()
    entries = parse_container(raw)
    text_count = 0
    for e in entries:
        try:
            e["data"].decode("utf-8")
            text_count += 1
        except UnicodeDecodeError:
            pass
    print("VALID PVZOL clientDB container")
    print(f"  Size: {len(raw):,} bytes")
    print(f"  Entries: {len(entries):,}")
    print(f"  UTF-8 text entries: {text_count:,}")
    print(f"  SHA-256: {sha256_file(path)}")


def menu() -> None:
    while True:
        print("\nPVZ Online clientDB Translation Tool")
        print("====================================")
        print("1. Extract for translation")
        print("2. Rebuild translated clientDB.txt")
        print("3. Verify clientDB.txt")
        print("4. Exit")
        choice = input("\nChoose 1-4: ").strip()
        try:
            if choice == "1":
                src = Path(input("Path to clientDB.txt: ").strip().strip('"'))
                dst_s = input("Output folder [clientDB_extracted]: ").strip().strip('"')
                dst = Path(dst_s or "clientDB_extracted")
                extract(src, dst, DEFAULT_CHUNK_BYTES)
            elif choice == "2":
                src_s = input("Extracted folder [clientDB_extracted]: ").strip().strip('"')
                dst_s = input("Output file [clientDB_translated.txt]: ").strip().strip('"')
                repack(Path(src_s or "clientDB_extracted"), Path(dst_s or "clientDB_translated.txt"))
            elif choice == "3":
                verify(Path(input("Path to clientDB.txt: ").strip().strip('"')))
            elif choice == "4":
                return
            else:
                print("Choose 1, 2, 3, or 4.")
        except Exception as exc:
            print(f"\nERROR: {exc}")


def main() -> None:
    if len(sys.argv) == 1:
        menu()
        return

    p = argparse.ArgumentParser(description="Translate and rebuild PVZOL clientDB.txt")
    sub = p.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("input", type=Path)
    ex.add_argument("output_dir", type=Path)
    ex.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    rp = sub.add_parser("repack")
    rp.add_argument("input_dir", type=Path)
    rp.add_argument("output", type=Path)
    rp.add_argument("--force", action="store_true")
    vf = sub.add_parser("verify")
    vf.add_argument("input", type=Path)
    args = p.parse_args()

    if args.command == "extract":
        if args.chunk_bytes <= 0:
            p.error("--chunk-bytes must be greater than zero")
        extract(args.input, args.output_dir, args.chunk_bytes)
    elif args.command == "repack":
        repack(args.input_dir, args.output, args.force)
    elif args.command == "verify":
        verify(args.input)


if __name__ == "__main__":
    main()
