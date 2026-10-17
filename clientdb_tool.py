#!/usr/bin/env python3
"""Repository-safe entry point for the PVZ Online clientDB tool.

The legacy clientdb_tool.py lives in "modding the game/", a directory this
repository's AGENTS.md explicitly protects from edits. This root-level entry
point keeps the existing extract/repack/verify commands available and adds the
new translation-sync workflow without changing the protected file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from clientdb_translate_sync import apply_plan, plan_sync, public_report

ROOT = Path(__file__).resolve().parent
LEGACY_TOOL = ROOT / "modding the game" / "clientdb_tool.py"


def load_legacy_tool():
    if not LEGACY_TOOL.is_file():
        raise FileNotFoundError(f"Existing clientDB tool was not found: {LEGACY_TOOL}")
    spec = importlib.util.spec_from_file_location("pvzol_legacy_clientdb_tool", LEGACY_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load existing clientDB tool: {LEGACY_TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_sync(old_workspace: str, updated_workspace: str, apply: bool = False) -> int:
    plan = plan_sync(old_workspace, updated_workspace)
    print(json.dumps(public_report(plan), ensure_ascii=False, indent=2))
    if apply:
        backup = apply_plan(plan)
        if backup:
            print(f"\nApplied {len(plan['changed'])} file update(s). Backup: {backup}")
        else:
            print("\nNothing to update.")
    else:
        print("\nDry run only. Use --apply to write changes.")
    return 0


def interactive_sync() -> None:
    print("\nMerge old translations into a newer clientDB workspace")
    print("The updated workspace stays authoritative; new rows remain untranslated.")
    old = input("Older translated workspace (or its translate folder): ").strip().strip('"')
    updated = input("Newer extracted workspace (or its translate folder): ").strip().strip('"')
    plan = plan_sync(old, updated)
    print(json.dumps(public_report(plan), ensure_ascii=False, indent=2))
    if not plan["changed"]:
        print("\nNo existing translations need to be copied.")
        return
    answer = input("\nApply these changes? A backup will be created first [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        print("No files changed.")
        return
    backup = apply_plan(plan)
    if backup:
        print(f"Applied {len(plan['changed'])} file update(s). Backup: {backup}")


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in {"sync", "sync-translations"}:
        # Delegate to the focused sync CLI. Drop the dispatcher subcommand.
        sys.argv = [str(ROOT / "clientdb_translate_sync.py"), *args[1:]]
        from clientdb_translate_sync import main as sync_main
        return sync_main()

    legacy = load_legacy_tool()
    if args:
        # Keep all original CLI forms working: extract, repack, and verify.
        sys.argv = [str(LEGACY_TOOL), *args]
        legacy.main()
        return 0

    while True:
        print("\nPVZ Online clientDB Tool (updated entry point)")
        print("==============================================")
        print("1. Existing extract / repack / verify menu")
        print("2. Merge old translations into a newer workspace")
        print("3. Exit")
        choice = input("Choose 1-3: ").strip()
        try:
            if choice == "1":
                sys.argv = [str(LEGACY_TOOL)]
                legacy.main()
            elif choice == "2":
                interactive_sync()
            elif choice == "3":
                return 0
            else:
                print("Choose 1, 2, or 3.")
        except Exception as exc:
            print(f"\nERROR: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
