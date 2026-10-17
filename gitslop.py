#!/usr/bin/env python3
"""
Simple Git Sync Tool for PvZOL
===============================
• Push: Stages, commits, and uploads all changes in 1 step.
• Pull: Downloads and updates files from GitHub.
• Status: Shows which files were edited.
"""

import sys
import subprocess
import datetime


def _decode_git_output(value):
    """Decode captured Git output safely on Windows and other platforms."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value.decode("utf-8", errors="replace").strip()


def run_git_cmd(args):
    """Run a git command and return (success, stdout, stderr).

    Capture bytes rather than letting subprocess decode pipes with the machine's
    default code page (e.g. cp932). Git output may contain UTF-8 paths/messages,
    and a decode error in subprocess' reader thread can leave stderr as None.
    """
    try:
        res = subprocess.run(
            ['git'] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        stdout = _decode_git_output(getattr(res, 'stdout', None))
        stderr = _decode_git_output(getattr(res, 'stderr', None))
        return res.returncode == 0, stdout, stderr
    except FileNotFoundError:
        return False, "", "Git is not installed or not found in system PATH."
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", "Could not run Git: %s" % exc


def check_status():
    """Show brief status, excluding volatile launcher cache files."""
    ok, out, err = run_git_cmd([
        'status', '-s', '--', '.', ':(exclude,glob)**/launcher_cache/**'
    ])
    if not ok:
        print(f"  [ERROR] {err}")
        return False
    if not out:
        print("  Everything is clean! No files have been changed.")
    else:
        print("  Modified files:")
        for line in out.splitlines():
            print(f"    {line}")
    return True


def do_push(custom_msg=None):
    """Add all, commit, and push."""
    print("\n[1/3] Checking for changes...")
    ok, out, err = run_git_cmd([
        'status', '-s', '--', '.', ':(exclude,glob)**/launcher_cache/**'
    ])
    if not ok:
        print(f"  [ERROR] Could not check Git status: {err or out}")
        return
    if not out:
        print("  Nothing to push! No files have been changed.")
        return

    # Determine commit message
    if custom_msg:
        msg = custom_msg
    else:
        default_msg = f"Update {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        msg = input(f"Enter commit message [default: '{default_msg}']: ").strip()
        if not msg:
            msg = default_msg

    print("\n[2/3] Staging and committing files...")

    # The game embeds a Chromium/WebView profile in launcher_cache. Its live
    # Cookies database can be locked while the game is running, and cache
    # contents should not be uploaded to GitHub anyway. Clear any cache paths
    # left staged by an earlier failed attempt, then exclude them from staging.
    run_git_cmd([
        'reset', '-q', 'HEAD', '--', ':(glob)**/launcher_cache/**'
    ])
    ok, _, err = run_git_cmd([
        'add', '-A', '--', '.', ':(exclude,glob)**/launcher_cache/**'
    ])
    if not ok:
        print(f"  [ERROR] Failed to add files: {err}")
        return

    # A status change may consist only of runtime cache files. Do not create an
    # empty commit in that case. Preserve any other already-staged changes.
    ok, staged, err = run_git_cmd(['diff', '--cached', '--name-only'])
    if not ok:
        print(f"  [ERROR] Could not inspect staged changes: {err or staged}")
        return
    if not staged:
        print("  No non-cache changes are staged; nothing to commit.")
        return

    ok, out, err = run_git_cmd(['commit', '-m', msg])
    if not ok:
        print(f"  [ERROR] Commit failed: {err or out}")
        return
    print(f'  Committed: "{msg}"')

    print("\n[3/3] Uploading to GitHub...")
    ok, out, err = run_git_cmd(['push'])
    if ok:
        print("  [SUCCESS] All changes successfully uploaded to GitHub!")
    else:
        # Check if first push needing upstream setup
        if "no upstream branch" in err or "set-upstream" in err:
            print("  Setting upstream branch (first push)...")
            ok_up, out_up, err_up = run_git_cmd(['push', '-u', 'origin', 'main'])
            if ok_up:
                print("  [SUCCESS] All changes successfully uploaded to GitHub!")
            else:
                print(f"  [ERROR] Push failed: {err_up or out_up}")
        else:
            print(f"  [ERROR] Push failed: {err or out}")


def do_pull():
    """Pull latest changes from GitHub."""
    print("\n[1/1] Downloading updates from GitHub...")
    ok, out, err = run_git_cmd(['pull'])
    if ok:
        print(f"  [SUCCESS] {out or 'Already up to date.'}")
    else:
        print(f"  [ERROR] Pull failed: {err or out}")


def main():
    print("=" * 50)
    print("         Simple GitHub Sync Tool")
    print("=" * 50)

    # 1. Quick CLI one-liners:
    # python git_sync.py push "fixed bean bug"
    # python git_sync.py pull
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == 'push':
            msg = sys.argv[2] if len(sys.argv) > 2 else None
            do_push(msg)
            return
        elif cmd == 'pull':
            do_pull()
            return
        elif cmd == 'status':
            check_status()
            return

    # 2. Interactive Menu (if run directly or double-clicked)
    while True:
        print("\nSelect an action:")
        print("  [1] PUSH   (Save & upload all changes to GitHub)")
        print("  [2] PULL   (Download latest files from GitHub)")
        print("  [3] STATUS (View which files were edited)")
        print("  [4] EXIT")

        choice = input("\nEnter choice (1-4): ").strip()

        if choice == '1':
            do_push()
        elif choice == '2':
            do_pull()
        elif choice == '3':
            print("\nRepository status:")
            check_status()
        elif choice in ('4', 'q', 'exit'):
            print("\nExiting. Goodbye!")
            break
        else:
            print("Invalid choice, please enter 1, 2, 3, or 4.")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")