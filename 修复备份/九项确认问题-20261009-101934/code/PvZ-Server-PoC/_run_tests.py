"""Throwaway: run every tests/test_*.py in its own process and summarise."""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
passed, failed = [], []
for path in sorted(glob.glob(os.path.join(HERE, "tests", "test_*.py"))):
    name = os.path.basename(path)
    proc = subprocess.run([PY, path], cwd=HERE, capture_output=True, text=True, timeout=600)
    ok = proc.returncode == 0
    (passed if ok else failed).append(name)
    if not ok:
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        print("FAIL %-45s %s" % (name, tail[-1] if tail else ""))

print("\npassed %d / %d" % (len(passed), len(passed) + len(failed)))
if failed:
    print("failed:", ", ".join(failed))
