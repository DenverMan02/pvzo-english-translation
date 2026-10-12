import os
import re
import sys
import argparse

# Comprehensive CJK unified ideographs range
CHINESE_PATTERN = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')

# Common binary extensions to skip automatically
BINARY_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp',
    '.swf', '.flv', '.mp3', '.ogg', '.wav', '.exe', '.dll',
    '.zip', '.rar', '.7z', '.tar', '.gz', '.bin', '.dat', '.pak'
}

def read_file_safe(filepath):
    """Attempts to read file with UTF-8, then GB18030/GBK, then UTF-16."""
    encodings = ['utf-8', 'gb18030', 'utf-16']
    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return f.read(), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, None

def is_binary(filepath):
    """Checks file extension or looks for null bytes."""
    _, ext = os.path.splitext(filepath)
    if ext.lower() in BINARY_EXTS:
        return True
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(1024)
            if b'\x00' in chunk:
                return True
    except Exception:
        return True
    return False

def scan_file(filepath):
    """Returns (lines_with_chinese, total_chinese_chars, encoding)."""
    if is_binary(filepath):
        return None

    content, encoding = read_file_safe(filepath)
    if content is None:
        return None

    matches = []
    total_chars = 0
    for line_num, line in enumerate(content.splitlines(), start=1):
        found = CHINESE_PATTERN.findall(line)
        if found:
            total_chars += len(found)
            matches.append((line_num, line.strip(), len(found)))

    return matches, total_chars, encoding

def main():
    parser = argparse.ArgumentParser(
        description="Scan files and folders for Chinese characters."
    )
    parser.add_argument("path", help="Folder or file path to scan")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show all matching lines (default shows up to 3 previews per file)"
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Save report to a text file"
    )
    parser.add_argument(
        "--ext", type=str, default=None,
        help="Filter by specific file extensions comma-separated (e.g. txt,tsv,json,xml)"
    )

    args = parser.parse_args()
    target_path = args.path

    if not os.path.exists(target_path):
        print(f"Error: Path does not exist: {target_path}")
        sys.exit(1)

    target_extensions = None
    if args.ext:
        target_extensions = {
            f".{ext.strip().lstrip('.').lower()}"
            for ext in args.ext.split(",")
        }

    # Collect files
    file_list = []
    if os.path.isfile(target_path):
        file_list.append(target_path)
    else:
        for root, _, files in os.walk(target_path):
            for file in files:
                _, ext = os.path.splitext(file)
                if target_extensions and ext.lower() not in target_extensions:
                    continue
                file_list.append(os.path.join(root, file))

    output_lines = []
    def log(msg=""):
        print(msg)
        output_lines.append(msg)

    log("=" * 60)
    log(f"Scanning target: {target_path}")
    log(f"Total files to inspect: {len(file_list)}")
    log("=" * 60)

    files_with_chinese = 0
    total_untranslated_chars = 0
    total_untranslated_lines = 0

    for filepath in file_list:
        result = scan_file(filepath)
        if not result:
            continue

        matches, total_chars, enc = result
        if matches:
            files_with_chinese += 1
            total_untranslated_chars += total_chars
            total_untranslated_lines += len(matches)

            log(f"\n[FOUND] {filepath} ({enc.upper()})")
            log(f"        -> {len(matches)} lines | {total_chars} Chinese characters")

            preview_limit = len(matches) if args.verbose else min(3, len(matches))
            for line_num, snippet, char_count in matches[:preview_limit]:
                # Truncate very long lines in output
                display_snippet = snippet if len(snippet) < 120 else snippet[:117] + "..."
                log(f"        L{line_num:4d} ({char_count:2d} chars): {display_snippet}")

            if not args.verbose and len(matches) > 3:
                log(f"        ... and {len(matches) - 3} more lines (use -v to see all)")

    log("\n" + "=" * 60)
    log("SCAN SUMMARY")
    log("=" * 60)
    log(f"Files scanned:              {len(file_list)}")
    log(f"Files with Chinese:         {files_with_chinese}")
    log(f"Total untranslated lines:   {total_untranslated_lines}")
    log(f"Total Chinese characters:   {total_untranslated_chars}")

    if files_with_chinese == 0:
        log("\n Clean! No Chinese text found in the scanned files.")
    else:
        log(f"\n Found Chinese text in {files_with_chinese} file(s).")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as out_f:
            out_f.write("\n".join(output_lines) + "\n")
        print(f"\nSaved report to: {args.output}")

if __name__ == "__main__":
    main()