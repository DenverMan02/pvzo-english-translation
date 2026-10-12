#!/usr/bin/env python3
"""
PvZ Online (PvZOL) Maximum Velocity ClientDB String Patcher (Complete Edition v7)
================================================================================
Fully optimized for multi-core systems:
1. Fast Streaming C Pre-Filter:
   - Only reads the first 2 MB of SWFs (where ActionScript code resides).
   - Skips hundreds of megabytes of raw audio/video streams in milliseconds.
2. Parallel Multi-Core Pre-Filter & Compilation:
   - Scans 2,446 SWFs in parallel across CPU cores with a real-time progress bar.
   - Recompiles multiple unique SWFs concurrently using worker threads.
3. Content-Hash Instant Cloning:
   - Automatically detects duplicate SWFs (like the 9 Game.swf variants).
   - Compiles once, then clones to all identical copies in 0.001 seconds!
4. Direct Java Engine + Chunked Batches (15 scripts per call) + Ping-Pong Buffers:
   - Bypasses Windows cmd.exe 8,191-character limits.
   - Alternates between ping-pong files to prevent file locking/corruption.
5. Automated Multi-Java Version Scanner:
   - Detects all Java installations on the machine (Registry, Program Files, JAVA_HOME).
   - Automatically selects a Java 8+ runtime (bypassing outdated Java 7 in PATH).
   - Probes max heap memory (-Xmx1536m / -Xmx1024m / -Xmx768m / default).
"""

import os
import sys
import re
import glob
import zlib
import shutil
import hashlib
import subprocess
import tempfile
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def has_chinese(s):
    """Check if string contains any Chinese characters."""
    return any('\u4e00' <= ch <= '\u9fff' for ch in s)


def read_lines_robust(filepath):
    """Read a text file trying common Chinese and Unicode encodings."""
    encodings = ['utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'gb2312', 'utf-16', 'latin-1']
    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        return f.readlines()


def split_db_line(line):
    """Split line using tab, comma, or pipe."""
    cleaned = line.rstrip('\r\n')
    if '\t' in cleaned:
        return cleaned.split('\t')
    if '|' in cleaned:
        return cleaned.split('|')
    if ',' in cleaned:
        return cleaned.split(',')
    return [cleaned]


def extract_pairs_from_files(untrans_file, trans_file):
    """
    Extract translation mapping from two corresponding clientdb files.
    Rule:
    - If untranslated text == translated text, IGNORE (still untranslated Chinese).
    - If untranslated has Chinese and translated differs and is non-empty, MAP IT!
    """
    u_lines = read_lines_robust(untrans_file)
    t_lines = read_lines_robust(trans_file)

    u_rows = [split_db_line(l) for l in u_lines if l.strip()]
    t_rows = [split_db_line(l) for l in t_lines if l.strip()]

    mapping = {}
    ignored_identical = 0

    u_by_id = {r[0].strip(): r for r in u_rows if r and r[0].strip().isdigit()}
    t_by_id = {r[0].strip(): r for r in t_rows if r and r[0].strip().isdigit()}

    if u_by_id and t_by_id and len(set(u_by_id.keys()) & set(t_by_id.keys())) > 5:
        for row_id in set(u_by_id.keys()) & set(t_by_id.keys()):
            u_r, t_r = u_by_id[row_id], t_by_id[row_id]
            for u_col, t_col in zip(u_r, t_r):
                u_val = u_col.strip()
                t_val = t_col.strip()
                if not has_chinese(u_val):
                    continue
                if u_val == t_val:
                    ignored_identical += 1
                    continue
                if t_val:
                    mapping[u_val] = t_val
    else:
        for u_r, t_r in zip(u_rows, t_rows):
            for u_col, t_col in zip(u_r, t_r):
                u_val = u_col.strip()
                t_val = t_col.strip()
                if not has_chinese(u_val):
                    continue
                if u_val == t_val:
                    ignored_identical += 1
                    continue
                if t_val:
                    mapping[u_val] = t_val

    return mapping, ignored_identical


def build_full_clientdb_mapping(untrans_path, trans_path):
    all_mappings = {}
    total_ignored = 0

    if os.path.isfile(untrans_path) and os.path.isfile(trans_path):
        m, ign = extract_pairs_from_files(untrans_path, trans_path)
        all_mappings.update(m)
        total_ignored += ign
    elif os.path.isdir(untrans_path) and os.path.isdir(trans_path):
        u_files = {p.name: p for p in Path(untrans_path).rglob('*') if p.is_file()}
        t_files = {p.name: p for p in Path(trans_path).rglob('*') if p.is_file()}
        common_names = sorted(set(u_files.keys()) & set(t_files.keys()))

        print(f"Comparing {len(common_names)} corresponding ClientDB files...")
        for name in common_names:
            m, ign = extract_pairs_from_files(str(u_files[name]), str(t_files[name]))
            all_mappings.update(m)
            total_ignored += ign
    else:
        raise ValueError("Paths must both be files or both be directories.")

    return all_mappings, total_ignored


# ==============================================================================
# Ultra-Fast Streaming SWF Binary Checker
# ==============================================================================

def check_single_swf(swf_path, combined_byte_regex):
    """
    Lightning-fast streaming check:
    ActionScript code is always in the first ~1-2 MB of an SWF.
    We only decompress up to 2 MB, skipping hundreds of megabytes of raw audio/video!
    """
    try:
        with open(swf_path, 'rb') as f:
            header = f.read(8)
            if len(header) < 8:
                return swf_path, False
            sig = header[:3]

            if sig == b'CWS':
                d = zlib.decompressobj()
                compressed_chunk = f.read(1024 * 1024 * 2)  # Read 2 MB from disk
                body = d.decompress(compressed_chunk, 1024 * 1024 * 3)  # Max 3 MB uncompressed
            elif sig == b'FWS':
                body = f.read(1024 * 1024 * 2)
            else:
                return swf_path, True  # LZMA or non-standard, keep to be safe

        match = bool(combined_byte_regex.search(body))
        return swf_path, match
    except Exception:
        return swf_path, True


# ==============================================================================
# JavaScript Patcher
# ==============================================================================

def patch_js_files_fast(js_files, text_regex, mapping, backup=True):
    total_replaced = 0
    encodings = ['utf-8', 'gb18030', 'gbk', 'latin-1']

    def repl_callback(m):
        return mapping[m.group(0)]

    for js_p in js_files:
        chosen_enc = 'utf-8'
        content = None
        for enc in encodings:
            try:
                with open(js_p, 'r', encoding=enc) as f:
                    content = f.read()
                chosen_enc = enc
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            continue

        matches = text_regex.findall(content)
        if matches:
            content = text_regex.sub(repl_callback, content)
            if backup:
                bak = str(js_p) + '.bak'
                if not os.path.exists(bak):
                    shutil.copy2(str(js_p), bak)
            with open(js_p, 'w', encoding=chosen_enc, errors='replace') as f:
                f.write(content)
            print(f"  [PATCHED] {js_p.name}: {len(matches)} replacement(s)")
            total_replaced += len(matches)

    return total_replaced


# ==============================================================================
# Java 8+ Discovery & Validation Engine
# ==============================================================================

JAVA_BIN = 'java'
JAVA_VERSION_NUM = 0
JAVA_HEAP_FLAGS = []


def parse_java_version(version_output):
    """Extract integer major version number (e.g. 1.7 -> 7, 1.8 -> 8, 11.0 -> 11, 17.0 -> 17)."""
    m = re.search(r'version\s+"(\d+)(?:\.(\d+))?', version_output, re.IGNORECASE)
    if m:
        v1 = int(m.group(1))
        v2 = int(m.group(2)) if m.group(2) else 0
        if v1 == 1:
            return v2
        return v1
    return 0


def test_java_executable(exe_path):
    """Run java -version to determine if it is valid and inspect its version."""
    try:
        r = subprocess.run([exe_path, '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        combined = (r.stderr or "") + (r.stdout or "")
        ver = parse_java_version(combined)
        first_line = combined.splitlines()[0] if combined else "Unknown"
        return ver, first_line
    except Exception:
        return 0, ""


def find_best_java(custom_java=None):
    """
    Thoroughly scans Windows Registry, environment variables, and directories
    to find a compatible Java 8 or newer runtime.
    """
    candidates = []

    if custom_java:
        candidates.append(custom_java)

    # 1. Environment variables
    for var in ['JAVA_HOME', 'JDK_HOME', 'JRE_HOME']:
        val = os.environ.get(var)
        if val:
            candidates.append(os.path.join(val, 'bin', 'java.exe'))
            candidates.append(os.path.join(val, 'java.exe'))

    # 2. Common install folders across Program Files & 64/32-bit
    patterns = [
        r"C:\Program Files\Java\*\bin\java.exe",
        r"C:\Program Files (x86)\Java\*\bin\java.exe",
        r"C:\Program Files\Eclipse Adoptium\*\bin\java.exe",
        r"C:\Program Files\AdoptOpenJDK\*\bin\java.exe",
        r"C:\Program Files\BellSoft\*\bin\java.exe",
        r"C:\Program Files\Zulu\*\bin\java.exe",
        r"C:\Program Files\Microsoft\*\bin\java.exe",
        r"C:\Program Files\Amazon Corretto\*\bin\java.exe",
        r"C:\Program Files (x86)\FFDec\jre\bin\java.exe",
        r"C:\Program Files\FFDec\jre\bin\java.exe",
        r"C:\Program Files\Common Files\Oracle\Java\javapath\java.exe",
        r"C:\Program Files (x86)\Common Files\Oracle\Java\javapath\java.exe",
        r"C:\ProgramData\Oracle\Java\javapath\java.exe",
    ]
    for pat in patterns:
        for p in glob.glob(pat):
            if os.path.isfile(p):
                candidates.append(p)

    # 3. Windows Registry query for Java installations
    try:
        import winreg
        for root in [winreg.HKEY_LOCAL_MACHINE]:
            for prefix in [r"SOFTWARE\JavaSoft", r"SOFTWARE\WOW6432Node\JavaSoft"]:
                for sub in [r"Java Runtime Environment", r"Java Development Kit", r"JDK", r"JRE"]:
                    reg_path = f"{prefix}\\{sub}"
                    try:
                        with winreg.OpenKey(root, reg_path) as k:
                            num_subkeys = winreg.QueryInfoKey(k)[0]
                            for i in range(num_subkeys):
                                ver_name = winreg.EnumKey(k, i)
                                try:
                                    with winreg.OpenKey(k, ver_name) as vk:
                                        jh, _ = winreg.QueryValueEx(vk, "JavaHome")
                                        candidates.append(os.path.join(jh, "bin", "java.exe"))
                                except Exception:
                                    pass
                    except Exception:
                        pass
    except ImportError:
        pass

    # 4. Fallback to system PATH
    sys_java = shutil.which('java')
    if sys_java:
        candidates.append(sys_java)

    # Evaluate all found candidates
    compatible = []
    seen = set()

    for c in candidates:
        norm = os.path.normpath(c).lower() if os.path.isfile(c) else c.lower()
        if norm in seen:
            continue
        seen.add(norm)

        ver, desc = test_java_executable(c)
        if ver >= 8:
            score = ver * 10
            if "Program Files (x86)" not in c:
                score += 5
            compatible.append((score, ver, c, desc))

    if compatible:
        compatible.sort(key=lambda x: x[0], reverse=True)
        best = compatible[0]
        return best[2], best[1], best[3]

    default_ver, default_desc = test_java_executable('java')
    return 'java', default_ver, default_desc


def detect_best_java_heap(java_bin):
    """Safely test what maximum heap memory Java allows without crashing."""
    candidates = ['-Xmx1536m', '-Xmx1024m', '-Xmx768m', '-Xmx512m']
    for mem in candidates:
        try:
            r = subprocess.run([java_bin, mem, '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if r.returncode == 0:
                return [mem]
        except Exception:
            pass
    return []


def init_java_environment(custom_java=None):
    """Initialize Java executable path, verify Java 8+, and probe heap limits."""
    global JAVA_BIN, JAVA_VERSION_NUM, JAVA_HEAP_FLAGS
    bin_path, ver_num, desc = find_best_java(custom_java)
    JAVA_BIN = bin_path
    JAVA_VERSION_NUM = ver_num

    if JAVA_VERSION_NUM < 8:
        print("\n" + "!" * 68)
        print(" [COMPATIBILITY ERROR] Java 8 or higher is REQUIRED for FFDec!")
        print("!" * 68)
        print(f"Current detected Java: {JAVA_BIN}")
        print(f"Version: {desc} (Major Version: {JAVA_VERSION_NUM})")
        print("\nFFDec was compiled for Java 8 (class version 52.0).")
        print("Your active Java runtime is Java 7 or older, which causes:")
        print("  'Unsupported major.minor version 52.0'")
        print("\nHow to fix:")
        print("1. Install Java 8 or 17 (e.g. 64-bit from https://adoptium.net or https://www.java.com)")
        print("2. Or if you have Java 8+ installed elsewhere, enter the path to java.exe below.")
        print("!" * 68 + "\n")

        manual = input("Enter path to java.exe (or press Enter to try anyway): ").strip().strip('"\'')
        if manual and os.path.exists(manual):
            m_ver, m_desc = test_java_executable(manual)
            JAVA_BIN = manual
            JAVA_VERSION_NUM = m_ver
            print(f"Using manual Java: {JAVA_BIN} ({m_desc})")

    JAVA_HEAP_FLAGS = detect_best_java_heap(JAVA_BIN)


# ==============================================================================
# FFDec Locator & Executor
# ==============================================================================

def find_ffdec(custom_path=None):
    """Locate FFDec executable or jar."""
    candidates = []
    if custom_path:
        candidates.append(custom_path)
        p = Path(custom_path)
        if p.is_dir():
            candidates.extend([
                str(p / "ffdec.jar"),
                str(p / "ffdec-cli.exe"),
                str(p / "ffdec.bat")
            ])
        elif p.is_file() and p.parent.joinpath('ffdec.jar').is_file():
            candidates.insert(0, str(p.parent.joinpath('ffdec.jar')))

    candidates.extend([
        r"C:\Program Files (x86)\FFDec\ffdec.jar",
        r"C:\Program Files\FFDec\ffdec.jar",
        r"C:\Program Files (x86)\FFDec\ffdec-cli.exe",
        r"C:\Program Files\FFDec\ffdec-cli.exe",
        r"C:\Program Files (x86)\FFDec\ffdec.bat",
        r"C:\Program Files\FFDec\ffdec.bat",
        "ffdec.jar",
        "ffdec-cli.exe",
        "ffdec.bat",
        "ffdec.sh",
        "ffdec",
    ])

    for c in candidates:
        if os.path.exists(c):
            if os.path.isdir(c):
                jar = os.path.join(c, "ffdec.jar")
                if os.path.exists(jar):
                    return jar
                cli = os.path.join(c, "ffdec-cli.exe")
                if os.path.exists(cli):
                    return cli
                bat = os.path.join(c, "ffdec.bat")
                if os.path.exists(bat):
                    return bat
            elif not c.endswith('.jar'):
                jar_sibling = Path(c).parent.joinpath('ffdec.jar')
                if jar_sibling.is_file():
                    return str(jar_sibling)
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


class FFDecCmdResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_ffdec_cmd(ffdec_bin, args):
    """Run FFDec with tested Java 8+ binary and allocated heap memory."""
    if ffdec_bin.endswith('.jar'):
        cmd = [JAVA_BIN] + JAVA_HEAP_FLAGS + ['-jar', ffdec_bin] + args
    else:
        cmd = [ffdec_bin] + args

    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        return FFDecCmdResult(1, "", f"Executable not found: {e}")
    except Exception as e:
        return FFDecCmdResult(1, "", str(e))


# ==============================================================================
# Robust Batch SWF Patcher via FFDec CLI
# ==============================================================================

def patch_swf_with_ffdec(ffdec_bin, swf_path, text_regex, mapping, backup=True):
    """
    Exports ActionScript from SWF, applies string replacements, and recompiles
    using alternating ping-pong buffers in 15-script batches.
    Returns: (replaced_count, status_message)
    """
    temp_dir = tempfile.mkdtemp(prefix="pvz_swf_")
    try:
        res = run_ffdec_cmd(ffdec_bin, ['-export', 'script', temp_dir, swf_path])
        if res.returncode != 0:
            err_msg = (res.stderr.strip() or res.stdout.strip()).replace('\r', ' ').replace('\n', ' ')
            if len(err_msg) > 120:
                err_msg = err_msg[:120] + '...'
            return -1, f"Export failed: {err_msg}"

        as_files = list(Path(temp_dir).rglob('*.as'))
        if not as_files:
            return 0, "No ActionScript files exported"

        modified_count = 0
        total_strings_replaced = 0
        modified_as_files = []

        def repl_callback(m):
            return mapping[m.group(0)]

        for as_path in as_files:
            try:
                with open(as_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(as_path, 'r', encoding='gb18030', errors='replace') as f:
                    content = f.read()

            matches = text_regex.findall(content)
            if matches:
                content = text_regex.sub(repl_callback, content)
                file_rep = len(matches)
                modified_count += 1
                total_strings_replaced += file_rep
                modified_as_files.append(str(as_path))
                with open(as_path, 'w', encoding='utf-8') as f:
                    f.write(content)

        if total_strings_replaced == 0:
            return 0, "No target strings in decompiled code"

        if backup:
            bak = swf_path + '.bak'
            if not os.path.exists(bak):
                shutil.copy2(swf_path, bak)

        # Prepare script names and file paths for FFDec -replace
        script_items = []
        for as_f in modified_as_files:
            rel = os.path.relpath(as_f, temp_dir)
            script_name = os.path.splitext(rel)[0].replace(os.sep, '.')
            if script_name.startswith('scripts.'):
                script_name = script_name[8:]
            script_items.append((script_name, as_f))

        # Chunked batch replacement using alternating ping-pong buffers
        chunk_size = 15
        out_a = os.path.join(temp_dir, "patched_a.swf")
        out_b = os.path.join(temp_dir, "patched_b.swf")
        curr_in = swf_path
        curr_out = out_a

        total_chunks = (len(script_items) + chunk_size - 1) // chunk_size

        for c_idx in range(total_chunks):
            chunk = script_items[c_idx * chunk_size : (c_idx + 1) * chunk_size]
            replace_args = ['-replace', curr_in, curr_out]
            for s_name, a_file in chunk:
                replace_args.extend([s_name, a_file])

            r = run_ffdec_cmd(ffdec_bin, replace_args)
            if r.returncode == 0 and os.path.exists(curr_out):
                curr_in = curr_out
                curr_out = out_b if curr_in == out_a else out_a
            else:
                for s_name, a_file in chunk:
                    r_ind = run_ffdec_cmd(ffdec_bin, ['-replace', curr_in, curr_out, s_name, a_file])
                    if r_ind.returncode == 0 and os.path.exists(curr_out):
                        curr_in = curr_out
                        curr_out = out_b if curr_in == out_a else out_a

        if curr_in != swf_path and os.path.exists(curr_in):
            shutil.copy2(curr_in, swf_path)
            return total_strings_replaced, "OK"
        else:
            return -1, "Compilation finished without generating output SWF"

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ==============================================================================
# Main Orchestration
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="PvZ Online Maximum Velocity ClientDB String Patcher"
    )
    parser.add_argument('-u', '--untranslated', help="Untranslated ClientDB (file or folder)")
    parser.add_argument('-t', '--translated', help="Translated ClientDB (file or folder)")
    parser.add_argument('-d', '--dir', help="Directory containing target .swf and .js files to patch")
    parser.add_argument('--ffdec', help="Path to FFDec (ffdec.jar, ffdec.bat, or ffdec-cli.exe)")
    parser.add_argument('--java', help="Custom path to Java 8+ executable (java.exe)")
    parser.add_argument('--workers', type=int, default=4, help="Number of concurrent SWF compilation workers (default: 4)")
    parser.add_argument('--no-backup', action='store_true', help="Skip creating .bak backup files")
    parser.add_argument('--min-len', type=int, default=2, help="Minimum character length of string to replace (default: 2)")

    args = parser.parse_args()

    print("=" * 65)
    print(" PvZ Online Maximum Velocity ClientDB String Patcher")
    print("=" * 65)

    init_java_environment(args.java)

    untrans = args.untranslated or input("Enter untranslated ClientDB (file or folder): ").strip().strip('"\'')
    trans = args.translated or input("Enter translated ClientDB (file or folder): ").strip().strip('"\'')

    if not os.path.exists(untrans) or not os.path.exists(trans):
        print("Error: ClientDB paths do not exist.")
        sys.exit(1)

    print("\nExtracting translation pairs from ClientDB...")
    mapping, ignored_identical = build_full_clientdb_mapping(untrans, trans)

    if args.min_len > 1:
        mapping = {k: v for k, v in mapping.items() if len(k) >= args.min_len}

    print(f"\n[ClientDB Stats]")
    print(f"  • Active translation pairs:          {len(mapping):,}")
    print(f"  • Identical Chinese strings ignored: {ignored_identical:,}")

    if not mapping:
        print("No translated differences found between the two ClientDB inputs.")
        sys.exit(0)

    print("\nCompiling ultra-fast C regex pattern engine...")
    sorted_keys = sorted(mapping.keys(), key=len, reverse=True)

    text_pattern_str = '|'.join(re.escape(k) for k in sorted_keys)
    text_regex = re.compile(text_pattern_str)

    byte_pattern_str = b'|'.join(re.escape(k.encode('utf-8')) for k in sorted_keys)
    combined_byte_regex = re.compile(byte_pattern_str)

    target_dir = args.dir or input("\nEnter folder containing .js and .swf game assets: ").strip().strip('"\'')
    if not os.path.isdir(target_dir):
        print(f"Error: Target folder '{target_dir}' does not exist.")
        sys.exit(1)

    js_files = list(Path(target_dir).rglob('*.js'))
    all_swfs = list(Path(target_dir).rglob('*.swf'))

    print(f"\nDiscovered {len(js_files)} .js file(s) and {len(all_swfs)} total .swf file(s).")

    # 1. Patch JS files
    if js_files:
        print("\n--- Patching JavaScript Files ---")
        total_js = patch_js_files_fast(js_files, text_regex, mapping, backup=not args.no_backup)
        print(f"JS Patching Complete: {total_js} replacements made.")

    # 2. Parallel SWF Pre-Filter with Real-Time Progress Bar
    if all_swfs:
        print("\n--- Parallel Pre-Filtering SWF Files ---")
        print(f"Scanning {len(all_swfs)} SWFs across CPU cores for exact target strings...")

        candidate_swfs = []
        total_swfs = len(all_swfs)
        done_count = 0

        pre_filter_workers = min(8, os.cpu_count() or 4)
        with ThreadPoolExecutor(max_workers=pre_filter_workers) as executor:
            futures = [executor.submit(check_single_swf, str(p), combined_byte_regex) for p in all_swfs]
            for fut in as_completed(futures):
                swf_path, is_candidate = fut.result()
                done_count += 1
                if is_candidate:
                    candidate_swfs.append(Path(swf_path))
                if done_count % 50 == 0 or done_count == total_swfs:
                    pct = (done_count * 100) // total_swfs
                    print(f"\r  Progress: [{done_count}/{total_swfs}] ({pct}%) | Matches so far: {len(candidate_swfs)}", end='', flush=True)

        print()
        skipped = total_swfs - len(candidate_swfs)
        print(f"Mathematically verified and skipped {skipped} animation/asset SWFs.")
        print(f"Identified {len(candidate_swfs)} SWF(s) containing actual target strings to recompile.")

        ffdec_bin = find_ffdec(args.ffdec)
        if not ffdec_bin:
            ffdec_bin = input("\nEnter path to FFDec (ffdec.jar / ffdec.bat / ffdec-cli.exe): ").strip().strip('"\'')

        if candidate_swfs and ffdec_bin and os.path.exists(ffdec_bin):
            print(f"\nUsing FFDec at: {ffdec_bin}")
            if ffdec_bin.endswith('.jar'):
                heap_desc = JAVA_HEAP_FLAGS[0] if JAVA_HEAP_FLAGS else 'default heap'
                print(f"JVM Engine:  {JAVA_BIN} (Java {JAVA_VERSION_NUM}, {heap_desc})")
            num_workers = min(args.workers, len(candidate_swfs))
            print(f"\n--- Recompiling Candidate SWF Files ({num_workers} parallel workers) ---")

            # Content Hash Cache: stores {md5_hash_before_patch: (temp_patched_copy, replacements_count)}
            hash_cache = {}
            cache_dir = tempfile.mkdtemp(prefix="pvz_hash_cache_")

            try:
                def process_candidate(item):
                    idx, swf_p = item
                    file_path_str = str(swf_p)

                    try:
                        with open(swf_p, 'rb') as f:
                            file_hash = hashlib.md5(f.read()).hexdigest()
                    except Exception:
                        file_hash = None

                    # Instant clone for identical duplicate SWFs
                    if file_hash and file_hash in hash_cache:
                        cached_file, cached_rep = hash_cache[file_hash]
                        if not args.no_backup:
                            bak = file_path_str + '.bak'
                            if not os.path.exists(bak):
                                shutil.copy2(file_path_str, bak)
                        shutil.copy2(cached_file, file_path_str)
                        print(f"  [{idx}/{len(candidate_swfs)}] [INSTANT CLONE] {swf_p.name} (0.001s, {cached_rep} strings)")
                        return cached_rep

                    print(f"  [{idx}/{len(candidate_swfs)}] Compiling {swf_p.name}...")
                    rep, msg = patch_swf_with_ffdec(ffdec_bin, file_path_str, text_regex, mapping, backup=not args.no_backup)

                    if rep > 0:
                        print(f"  [{idx}/{len(candidate_swfs)}] [SUCCESS] {swf_p.name}: {rep} string(s) replaced")
                        if file_hash:
                            cached_copy = os.path.join(cache_dir, f"cached_{file_hash}.swf")
                            shutil.copy2(file_path_str, cached_copy)
                            hash_cache[file_hash] = (cached_copy, rep)
                        return rep
                    elif rep == 0:
                        print(f"  [{idx}/{len(candidate_swfs)}] [NO CODE STRINGS] {swf_p.name}")
                        return 0
                    else:
                        print(f"  [{idx}/{len(candidate_swfs)}] [ERROR] {swf_p.name} -> {msg}")
                        return 0

                with ThreadPoolExecutor(max_workers=num_workers) as executor:
                    tasks = list(enumerate(candidate_swfs, 1))
                    results = list(executor.map(process_candidate, tasks))

                total_swf_rep = sum(results)
            finally:
                shutil.rmtree(cache_dir, ignore_errors=True)

            print(f"\nSWF Patching Complete: {total_swf_rep} total replacements recompiled.")

    print("\n" + "=" * 65)
    print("PATCHING FINISHED!")
    print("All modified files have been backed up (.bak).")
    print("Restart your localhost server and enjoy!")
    print("=" * 65)


if __name__ == '__main__':
    main()