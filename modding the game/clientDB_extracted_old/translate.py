import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

# ==================== CONFIGURATION ====================
API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAh58S-KOzMg7sbCamvPXDDoJ64Yu_A68A")
MODEL = "gemini-flash-lite-latest"  # Active, fast, high rate limits
CACHE_FILE = "gemini_translations.json"
BATCH_SIZE = 35
# ========================================================

cache = {}
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}

def save_cache():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def has_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def call_gemini_batch(phrases):
    """Sends a batch of Chinese phrases to Gemini with retry support."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={API_KEY}"
    
    system_prompt = (
        "You are a professional video game localizer specializing in Plants vs. Zombies (植物大战僵尸).\n"
        "Translate the provided Chinese game strings into natural, high-quality, game-accurate English.\n"
        "Guidelines:\n"
        "- Use official Plants vs. Zombies terminology (e.g., 豌豆射手 = Peashooter, 坚果 = Wall-nut, "
        "向日葵 = Sunflower, 樱桃炸弹 = Cherry Bomb, 窝瓜 = Squash, 巨人僵尸 = Gargantuar, 关卡 = Stage, 塔防 = Tower Defense).\n"
        "- Preserve placeholders and formatting tags strictly: {0}, {1}, %d, %s, \\n, <font...>, </font>.\n"
        "- Return a valid JSON object mapping each original Chinese string directly to its English translation."
    )

    prompt = system_prompt + "\n\nTranslate these phrases:\n" + json.dumps(phrases, ensure_ascii=False)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    # Retry up to 3 times for transient rate-limit or 503 spikes
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(raw_text)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"   [HTTP {e.code}]: {err_body[:100]}...")
            if e.code in (429, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return {}
        except Exception as e:
            print(f"   [Error]: {e}")
            return {}
    return {}

def process_file(filepath):
    print(f"\n==========================================")
    print(f"Processing: {filepath}")
    
    # 1. Read file and preserve original line endings (LF vs CRLF)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_content = f.read()
    except UnicodeDecodeError:
        with open(filepath, "r", encoding="gb18030") as f:
            raw_content = f.read()

    if not raw_content.strip():
        print("Empty file, skipping.")
        return

    # Detect line endings: preserve LF (\n) if present, fallback to LF
    line_ending = '\r\n' if '\r\n' in raw_content else '\n'
    lines = raw_content.splitlines()

    delimiter = "\t" if (lines and "\t" in lines[0]) else ","
    rows = list(csv.reader(lines, delimiter=delimiter))

    # 2. Find all unique Chinese strings not yet in cache
    to_translate = set()
    for r_idx, row in enumerate(rows):
        if r_idx == 0:
            continue
        for cell in row:
            text = cell.strip()
            if has_chinese(text) and text not in cache:
                to_translate.add(text)

    unique_list = list(to_translate)
    print(f"Total rows: {len(rows)} | New unique Chinese strings: {len(unique_list)}")
    print(f"Detected line ending: {'Windows (CRLF)' if line_ending == chr(13)+chr(10) else 'Unix (LF)'}")

    # 3. Batch translate
    if unique_list:
        for i in range(0, len(unique_list), BATCH_SIZE):
            chunk = unique_list[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (len(unique_list) - 1) // BATCH_SIZE + 1
            print(f"   Translating batch {batch_num} of {total_batches} ({len(chunk)} strings)...")
            
            result = call_gemini_batch(chunk)
            for original, translated in result.items():
                if translated:
                    cache[original] = translated
            
            save_cache()
            time.sleep(0.5)

    # 4. Inject translations back into rows
    translated_rows = []
    for r_idx, row in enumerate(rows):
        if r_idx == 0:
            translated_rows.append(row)
            continue
        new_row = []
        for cell in row:
            text = cell.strip()
            if has_chinese(text) and text in cache:
                new_row.append(cache[text])
            else:
                new_row.append(cell)
        translated_rows.append(new_row)

    # 5. Save output file using the EXACT line terminator detected
    base, ext = os.path.splitext(filepath)
    out_file = f"{base}_en{ext}"

    with open(out_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter, lineterminator=line_ending)
        writer.writerows(translated_rows)

    print(f"Done! Saved translated file to: {out_file}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python gemini_translate_tsv.py <file.txt>         (Single file)")
        print("  python gemini_translate_tsv.py <folder>           (All files in folder)")
        sys.exit(1)

    target = sys.argv[1]
    if os.path.isfile(target):
        process_file(target)
    elif os.path.isdir(target):
        for root, _, files in os.walk(target):
            for file in files:
                if file.lower().endswith((".txt", ".tsv", ".csv")) and not file.endswith("_en.txt"):
                    process_file(os.path.join(root, file))
    else:
        print(f"Path not found: {target}")