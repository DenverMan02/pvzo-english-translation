#!/usr/bin/env python3
import sys
import os
import subprocess
from datetime import datetime

def run_cmd(cmd):
    """Runs a shell command and streams the output."""
    print(f"\n> {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"[!] Error: Command failed with exit code {result.returncode}")
        return False
    return True

def get_current_branch():
    """Detects the current git branch name."""
    res = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
    branch = res.stdout.strip()
    return branch if branch else "main"

def pull(force=False):
    """Pull latest changes from the current branch and fetch LFS files.
    If force is True, fetches origin and hard-resets tracked files to match remote.
    """
    branch = get_current_branch()
    
    if force:
        print(f"--- FORCE PULL: Overwriting local tracked files with origin/{branch} ---")
        if not run_cmd(["git", "fetch", "origin"]):
            return False
        if not run_cmd(["git", "reset", "--hard", f"origin/{branch}"]):
            return False
        run_cmd(["git", "lfs", "pull"])
        if os.path.exists("tools/xfl_compiler.py"):
            print("--- Compiling/Updating plant animation assets ---")
            subprocess.run([sys.executable, "tools/xfl_compiler.py", "all"])
        print("[✓] Successfully force-synced with remote!")
        return True

    print(f"--- Pulling latest changes from {branch} ---")
    result = subprocess.run(["git", "pull"])
    if result.returncode != 0:
        print("\n[!] Standard 'git pull' failed (likely due to local uncommitted edits or conflict markers).")
        choice = input(f"Do you want to FORCE overwrite local files with origin/{branch}? (y/N): ").strip().lower()
        if choice in ("y", "yes"):
            return pull(force=True)
        return False

    # Ensures large binary files are fetched if any were updated
    run_cmd(["git", "lfs", "pull"])
    if os.path.exists("tools/xfl_compiler.py"):
        print("--- Compiling/Updating plant animation assets ---")
        subprocess.run([sys.executable, "tools/xfl_compiler.py", "all"])
    return True

def push(message=None):
    """Stage all files, commit, and push."""
    if not message:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = input(f"Enter commit message [Default: 'Sync {timestamp}']: ").strip()
        if not message:
            message = f"Sync {timestamp}"

    print(f"--- Staging, committing, and pushing: '{message}' ---")
    if not run_cmd(["git", "add", "-A"]):
        return False
    
    # Check if there are changes to commit
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if not status.stdout.strip():
        print("[i] Nothing new to commit. Pushing any unpushed commits...")
    else:
        if not run_cmd(["git", "commit", "-m", message]):
            return False

    return run_cmd(["git", "push"])

def status():
    """Show git status."""
    return run_cmd(["git", "status", "-s"])

def interactive_menu():
    while True:
        print("\n=== Git Sync Helper ===")
        print("1. Push (add, commit, push)")
        print("2. Pull (fetch & merge)")
        print("3. Force Pull (hard reset & overwrite with remote)")
        print("4. Status")
        print("5. Exit")
        choice = input("Select an option (1-5): ").strip()

        if choice == "1":
            push()
        elif choice == "2":
            pull()
        elif choice == "3":
            pull(force=True)
        elif choice == "4":
            status()
        elif choice in ("5", "q", "exit"):
            print("Goodbye!")
            break
        else:
            print("[!] Invalid option. Try again.")

def main():
    script_name = os.path.basename(sys.argv[0])

    if len(sys.argv) == 1:
        interactive_menu()
    else:
        command = sys.argv[1].lower()
        if command == "push":
            msg = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else None
            push(msg)
        elif command in ("pull", "force-pull", "forcepull"):
            # Check if user specified 'force' or '--force'
            is_force = (command in ("force-pull", "forcepull")) or (len(sys.argv) > 2 and sys.argv[2].lower() in ("force", "--force", "-f"))
            pull(force=is_force)
        elif command == "status":
            status()
        else:
            print("Usage:")
            print(f"  python3 {script_name}              (Interactive menu)")
            print(f"  python3 {script_name} pull         (Pull updates, prompt to force if conflict)")
            print(f"  python3 {script_name} pull force   (Force overwrite local with remote)")
            print(f"  python3 {script_name} push [msg]   (Stage, commit with optional message, and push)")
            print(f"  python3 {script_name} status       (Quick status check)")

if __name__ == "__main__":
    main()
