#!/usr/bin/env python3
"""Carry existing clientDB translations into a newer extracted workspace.

The source is the older, already-translated workspace. The target is a fresh
workspace extracted from the updated clientDB. The target remains the source of
truth for rows/files: existing translations are copied forward by table + row
ID, while newly introduced rows remain in the target's original language and
are reported as needing translation.

Pass either a workspace folder containing translate/ or the translate/ folder
itself. The default is a dry run; use --apply to write changes. Each apply makes
a timestamped backup of every file it changes.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

INDEX_PREFIX = re.compile(r"^\d{4}_")
ID_HEADER = "id"


def read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return f.read()


def line_body_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1:]
    return line, ""


def normalize_header(value: str) -> str:
    value = value.lstrip("\ufeff").strip().lower()
    return re.sub(r"[^a-z0-9]", "", value)


def resolve_translate_dir(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise NotADirectoryError(path)
    if path.name.lower() == "translate":
        return path
    child = path / "translate"
    if child.is_dir():
        return child
    if any(path.rglob("*.txt")):
        # Also accept a translate-like folder whose name is not literally
        # "translate", which is useful when users keep renamed copies.
        return path
    raise FileNotFoundError(
        f"{path} is not a workspace containing translate/ or a translate folder."
    )


def logical_file_key(relative_path: Path) -> str:
    parts = list(relative_path.parts)
    if parts:
        parts[-1] = INDEX_PREFIX.sub("", parts[-1], count=1)
    return Path(*parts).as_posix().casefold()


def index_files(directory: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        relative = path.relative_to(directory)
        key = logical_file_key(relative)
        if key in result:
            raise ValueError(
                f"Two files map to the same logical table {key!r}: "
                f"{result[key]} and {path}"
            )
        result[key] = path
    return result


def split_lines(text: str) -> list[tuple[str, str]]:
    return [line_body_ending(line) for line in text.splitlines(keepends=True)]


def looks_like_tsv(lines: list[tuple[str, str]]) -> bool:
    if not lines:
        return False
    header = lines[0][0]
    return "\t" in header and len(header.split("\t")) >= 2


def data_row_count(text: str) -> int:
    lines = split_lines(text)
    return max(0, len(lines) - 1) if looks_like_tsv(lines) else 0


def merge_translation_text(old_text: str, updated_text: str) -> dict[str, Any]:
    """Return updated text with old translations copied onto matching ID rows."""
    old_lines = split_lines(old_text)
    new_lines = split_lines(updated_text)

    if not looks_like_tsv(old_lines) or not looks_like_tsv(new_lines):
        return {
            "content": updated_text,
            "status": "manual-review",
            "matched_rows": 0,
            "new_rows": 0,
            "removed_old_rows": 0,
            "changed_cells": 0,
            "warning": "At least one file is not a tab-separated table; left it unchanged.",
        }

    old_headers = old_lines[0][0].split("\t")
    new_headers = new_lines[0][0].split("\t")
    old_normalized = [normalize_header(h) for h in old_headers]
    new_normalized = [normalize_header(h) for h in new_headers]

    old_unique = {h: i for i, h in enumerate(old_normalized)
                  if h and old_normalized.count(h) == 1}
    new_unique = {h: i for i, h in enumerate(new_normalized)
                  if h and new_normalized.count(h) == 1}

    old_key_col = old_unique.get(ID_HEADER, 0)
    new_key_col = new_unique.get(ID_HEADER, 0)
    common_columns = [
        (old_unique[h], new_unique[h])
        for h in old_unique.keys() & new_unique.keys()
        if h != ID_HEADER
    ]
    common_columns.sort(key=lambda pair: pair[1])

    if not common_columns:
        return {
            "content": updated_text,
            "status": "manual-review",
            "matched_rows": 0,
            "new_rows": max(0, len(new_lines) - 1),
            "removed_old_rows": max(0, len(old_lines) - 1),
            "changed_cells": 0,
            "warning": "No unambiguous shared translation columns; left it unchanged.",
        }

    old_rows: dict[str, deque[list[str]]] = defaultdict(deque)
    duplicate_counts: dict[str, int] = defaultdict(int)

    for line_no, (body, _) in enumerate(old_lines[1:], start=2):
        row = body.split("\t")
        if len(row) != len(old_headers):
            return {
                "content": updated_text,
                "status": "manual-review",
                "matched_rows": 0,
                "new_rows": max(0, len(new_lines) - 1),
                "removed_old_rows": max(0, len(old_lines) - 1),
                "changed_cells": 0,
                "warning": f"Source row {line_no} has {len(row)} columns; expected {len(old_headers)}.",
            }
        key = row[old_key_col].strip() if old_key_col < len(row) else ""
        if key:
            old_rows[key].append(row)
            duplicate_counts[key] += 1

    new_seen: dict[str, int] = defaultdict(int)
    matched_rows = new_rows = changed_cells = 0
    result_lines = list(new_lines)

    for line_idx, (body, ending) in enumerate(new_lines[1:], start=1):
        row = body.split("\t")
        if len(row) != len(new_headers):
            return {
                "content": updated_text,
                "status": "manual-review",
                "matched_rows": 0,
                "new_rows": max(0, len(new_lines) - 1),
                "removed_old_rows": max(0, len(old_lines) - 1),
                "changed_cells": 0,
                "warning": f"Target row {line_idx + 1} has {len(row)} columns; expected {len(new_headers)}.",
            }

        key = row[new_key_col].strip() if new_key_col < len(row) else ""
        if not key or not old_rows.get(key):
            new_rows += 1
            continue

        old_row = old_rows[key].popleft()
        new_seen[key] += 1
        matched_rows += 1
        for old_col, new_col in common_columns:
            if old_col >= len(old_row) or new_col >= len(row):
                continue
            if row[new_col] != old_row[old_col]:
                row[new_col] = old_row[old_col]
                changed_cells += 1
        result_lines[line_idx] = ("\t".join(row), ending)

    removed_old_rows = sum(len(rows) for rows in old_rows.values())
    ambiguous_ids = sorted(key for key, count in duplicate_counts.items() if count > 1)
    warnings = []
    if ambiguous_ids:
        warnings.append(
            "Repeated IDs were matched by occurrence order; review these tables if rows "
            "with the same ID were inserted in the middle: "
            + ", ".join(ambiguous_ids[:10])
            + ("…" if len(ambiguous_ids) > 10 else "")
        )

    content = "".join(body + ending for body, ending in result_lines)
    return {
        "content": content,
        "status": "updated" if changed_cells else "unchanged",
        "matched_rows": matched_rows,
        "new_rows": new_rows,
        "removed_old_rows": removed_old_rows,
        "changed_cells": changed_cells,
        "warning": " ".join(warnings) if warnings else None,
    }


def plan_sync(old_workspace: str | Path, updated_workspace: str | Path) -> dict[str, Any]:
    old_dir = resolve_translate_dir(old_workspace)
    updated_dir = resolve_translate_dir(updated_workspace)
    if old_dir == updated_dir:
        raise ValueError("The source and target translate folders must be different.")

    old_files = index_files(old_dir)
    updated_files = index_files(updated_dir)
    files: list[dict[str, Any]] = []
    for key, target_path in updated_files.items():
        source_path = old_files.get(key)
        relative = target_path.relative_to(updated_dir)
        if source_path is None:
            target_text = read_text(target_path)
            pending = data_row_count(target_text)
            files.append({
                "key": key,
                "path": relative.as_posix(),
                "target_path": target_path,
                "source_path": None,
                "content": None,
                "status": "new-file",
                "matched_rows": 0,
                "new_rows": pending,
                "removed_old_rows": 0,
                "changed_cells": 0,
                "warning": "No previous translation file; left updated strings unchanged.",
            })
            continue

        source_text = read_text(source_path)
        target_text = read_text(target_path)
        merged = merge_translation_text(source_text, target_text)
        files.append({
            "key": key,
            "path": relative.as_posix(),
            "target_path": target_path,
            "source_path": source_path,
            **merged,
        })

    old_only_files = sorted(set(old_files) - set(updated_files))
    changed = [f for f in files if f["content"] is not None and f["content"] != read_text(f["target_path"])]
    return {
        "old_dir": old_dir,
        "updated_dir": updated_dir,
        "files": files,
        "changed": changed,
        "old_only_files": old_only_files,
        "summary": {
            "updated_files": len(changed),
            "matched_files": sum(f["source_path"] is not None for f in files),
            "new_target_files": sum(f["status"] == "new-file" for f in files),
            "matched_rows": sum(f["matched_rows"] for f in files),
            "new_rows_needing_translation": sum(f["new_rows"] for f in files),
            "removed_old_rows": sum(f["removed_old_rows"] for f in files),
            "changed_cells": sum(f["changed_cells"] for f in files),
            "manual_review_files": sum(f["status"] == "manual-review" for f in files),
            "old_only_files": len(old_only_files),
        },
    }


def apply_plan(plan: dict[str, Any]) -> Path | None:
    changes = plan["changed"]
    if not changes:
        return None
    backup_root = plan["updated_dir"].parent / (
        ".clientdb-translate-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    backup_root.mkdir(parents=True, exist_ok=False)

    written: list[Path] = []
    try:
        for item in changes:
            target: Path = item["target_path"]
            relative = target.relative_to(plan["updated_dir"])
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)

        for item in changes:
            target = item["target_path"]
            temp = target.with_name(target.name + ".sync-tmp")
            try:
                with temp.open("w", encoding="utf-8", newline="") as f:
                    f.write(item["content"])
                temp.replace(target)
                written.append(target)
            finally:
                if temp.exists():
                    temp.unlink()
    except Exception:
        # Backups remain available for manual recovery if writing fails.
        raise
    return backup_root


def public_report(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "old_translations": str(plan["old_dir"]),
        "updated_workspace": str(plan["updated_dir"]),
        "summary": plan["summary"],
        "files": [
            {
                "file": item["path"],
                "status": item["status"],
                "matched_rows": item["matched_rows"],
                "new_rows": item["new_rows"],
                "removed_old_rows": item["removed_old_rows"],
                "changed_cells": item["changed_cells"],
                "warning": item["warning"],
            }
            for item in plan["files"]
        ],
        "old_only_files": plan["old_only_files"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge old English clientDB translations into a newer extracted workspace."
    )
    parser.add_argument(
        "old_translated_workspace",
        help="Older translated workspace, or its translate/ folder.",
    )
    parser.add_argument(
        "updated_workspace",
        help="Newer extracted workspace to update, or its translate/ folder.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is a dry run). Changed files are backed up first.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optional path to write the full comparison report as JSON.",
    )
    args = parser.parse_args()

    try:
        plan = plan_sync(args.old_translated_workspace, args.updated_workspace)
        print(json.dumps(public_report(plan), ensure_ascii=False, indent=2))
        if args.json_report:
            args.json_report.write_text(
                json.dumps(public_report(plan), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if args.apply:
            backup = apply_plan(plan)
            if backup:
                print(f"\nApplied {len(plan['changed'])} file update(s). Backup: {backup}")
            else:
                print("\nNothing to update.")
        else:
            print("\nDry run only. Re-run with --apply to write changes.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
