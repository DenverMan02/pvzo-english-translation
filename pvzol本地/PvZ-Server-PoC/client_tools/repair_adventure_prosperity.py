"""Patch only fuben.txt first-clear prosperity in clientDB, keeping other archive bytes intact.

The shipped ordinary/elite table has 440 stages per mode and a flat 160 first-clear reward.
No official progression table survived. This local rule interpolates 160..2800 in stage order;
it is not claimed to reproduce the original server formula. Both Flash and server read this table.
"""
import argparse
from pathlib import Path
import shutil

FIRST_REWARD = 160
LAST_REWARD = 2800
SERVER = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = SERVER.parent / "webroot" / "clientDB.txt"


def prosperity_at(index, total):
    if total <= 1:
        return FIRST_REWARD
    # Integer nearest rounding, without a floating point or Python banker's-rounding dependency.
    denominator = total - 1
    return FIRST_REWARD + ((LAST_REWARD - FIRST_REWARD) * index * 2 + denominator) // (2 * denominator)


def scale_fuben_table(payload):
    lines = payload.decode("utf-8").splitlines(keepends=True)
    header = lines[0].rstrip("\r\n").split("\t")
    col = {name: i for i, name in enumerate(header)}
    groups = {1: [], 2: []}
    for index, line in enumerate(lines[1:], 1):
        fields = line.rstrip("\r\n").split("\t")
        if not line.strip():
            continue
        mode = int(fields[col["mode"]])
        if mode not in groups:
            continue
        bonus = fields[col["bonusFirst"]].split(";")
        positions = [i for i, value in enumerate(bonus) if value.startswith("2:")]
        if len(positions) != 1:
            raise ValueError("Expected exactly one first-clear prosperity entry in row %d" % index)
        groups[mode].append((int(fields[col["chapterId"]]), int(fields[col["stageId"]]),
                             index, fields, bonus, positions[0]))
    changes = []
    for mode, stages in groups.items():
        stages.sort(key=lambda row: row[:2])
        if not stages:
            raise ValueError("No adventure stages for mode %d" % mode)
        old_values = [int(row[4][row[5]].split(":")[1]) for row in stages]
        planned = [prosperity_at(i, len(stages)) for i in range(len(stages))]
        if old_values == planned:
            continue  # idempotent: don't redistribute rewards when already patched
        if set(old_values) != {FIRST_REWARD}:
            raise ValueError("Mode %d already has non-flat rewards; refusing to overwrite them" % mode)
        for row, old, new in zip(stages, old_values, planned):
            chapter, stage, index, fields, bonus, position = row
            if old == new:
                continue
            original = lines[index]
            ending = original[len(original.rstrip("\r\n")):]
            bonus[position] = "2:%d" % new
            fields[col["bonusFirst"]] = ";".join(bonus)
            lines[index] = "\t".join(fields) + ending
            changes.append((chapter, mode, stage, old, new))
    return "".join(lines).encode("utf-8"), changes


def patch_archive(raw):
    pos, chunks, changes, found = 0, [], [], False
    while pos < len(raw):
        start = pos
        if pos + 4 > len(raw):
            raise ValueError("Truncated table-name length")
        name_len = int.from_bytes(raw[pos:pos + 4], "big")
        pos += 4
        if pos + name_len + 4 > len(raw):
            raise ValueError("Truncated table header")
        name = raw[pos:pos + name_len].decode("utf-8")
        pos += name_len
        length_pos = pos
        length = int.from_bytes(raw[pos:pos + 4], "big")
        pos += 4
        end = pos + length
        if end > len(raw):
            raise ValueError("Truncated table payload")
        if name == "fuben.txt":
            if found:
                raise ValueError("Duplicate fuben.txt table")
            found = True
            replacement, changes = scale_fuben_table(raw[pos:end])
            chunks.append(raw[start:length_pos] + len(replacement).to_bytes(4, "big") + replacement)
        else:
            chunks.append(raw[start:end])
        pos = end
    if not found:
        raise ValueError("Archive has no fuben.txt table")
    return b"".join(chunks), changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--apply", action="store_true", help="Otherwise preview without writing")
    args = parser.parse_args()
    raw = args.archive.read_bytes()
    patched, changes = patch_archive(raw)
    print("Changed first-clear prosperity entries:", len(changes))
    for chapter, mode, stage, old, new in changes:
        if stage == 15 and chapter in (16, 32) or chapter == 1 and stage == 2:
            print("  %d/%d/%d: %d -> %d" % (chapter, mode, stage, old, new))
    if args.apply and changes:
        backup_dir = SERVER / "backups" / "adventure-prosperity-20260929"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / "clientDB.before.txt"
        if backup.exists():
            raise FileExistsError("Backup already exists; refusing to replace it: %s" % backup)
        shutil.copy2(args.archive, backup)
        # Mechanical update of a proprietary binary archive; no unrelated tables are reserialized.
        args.archive.write_bytes(patched)
        print("Applied:", args.archive)
        print("Backup:", backup)
    elif not changes:
        print("Already repaired; no files changed.")
    else:
        print("Preview only. Use --apply to update the client/server shared table.")


if __name__ == "__main__":
    main()
