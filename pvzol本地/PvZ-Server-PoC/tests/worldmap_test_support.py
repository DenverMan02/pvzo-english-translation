"""Isolated map QA helpers; never open a supplied player save.

Copies Python sources into a temporary server root before any project import.
Only static webroot/Assets are referenced from the chosen working copy.
Static files use directory symlinks on Unix and read references through
junctions on Windows. No elevated privilege is required by this harness.
"""
from __future__ import annotations
import importlib
import gc
import logging
from contextlib import closing
from pathlib import Path
import shutil
import sys
import tempfile
import os
import subprocess


class IsolatedServer:
    def __init__(self, project_root=None, keep_dir=None):
        self.project = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        self.temporary = None
        if keep_dir is None:
            self.temporary = tempfile.TemporaryDirectory(prefix="pvzol_worldmap_qa_")
            self.root = Path(self.temporary.name)
        else:
            self.root = Path(keep_dir).resolve()
            self.root.mkdir(parents=True, exist_ok=False)
        self.server_dir = self.root / "PvZ-Server-PoC"
        self.server_dir.mkdir()
        self.sources = {p.stem for p in (self.project / "PvZ-Server-PoC").glob("*.py")}
        if any(name in sys.modules for name in self.sources):
            raise RuntimeError("Run QA in a fresh Python process: project modules already imported")
        for source in (self.project / "PvZ-Server-PoC").glob("*.py"):
            shutil.copy2(source, self.server_dir / source.name)
        # Static authored rosters are required by the game module; copying them
        # does not copy or open any player database.
        for name in ("adventure_archive_formations.json",):
            source = self.project / "PvZ-Server-PoC" / name
            if source.is_file():
                shutil.copy2(source, self.server_dir / name)
        for name in ("webroot", "Assets"):
            try:
                if os.name == 'nt':
                    fixture_link = self.root / name
                    fixture_target = self.project / name
                    # QA-only read reference: Windows junctions need no admin
                    # privilege and avoid copying the full static game per test.
                    result = subprocess.run(['powershell', '-NoProfile', '-Command',
                        "New-Item -ItemType Junction -Path '" + str(fixture_link).replace("'", "''")
                        + "' -Target '" + str(fixture_target).replace("'", "''") + "' | Out-Null"],
                        capture_output=True, text=True)
                    if result.returncode:
                        raise OSError(result.stderr)
                else:
                    (self.root / name).symlink_to(self.project / name, target_is_directory=True)
            except OSError:
                # Windows may disallow symlinks. Copy static fixtures only;
                # player databases remain synthetic and local to this QA run.
                shutil.copytree(self.project / name, self.root / name)
        sys.path.insert(0, str(self.server_dir))
        self.store = importlib.import_module("player_store")
        self.store.set_db_path(str(self.server_dir / "synthetic_players.db"))
        self.store.init_db()
        self._saved_log_level = logging.getLogger().level
        self._saved_log_handlers = list(logging.getLogger().handlers)
        for handler in self._saved_log_handlers:
            logging.getLogger().removeHandler(handler)
        self.game = importlib.import_module("pvz_poc_server")
        self.game.log.setLevel(logging.WARNING)
        assert Path(self.store.DB_PATH).is_relative_to(self.root)
        assert Path(self.game.LOG_PATH).is_relative_to(self.root)

    def player(self, uin, name="Cloud QA", **values):
        player, _ = self.store.get_or_create_player(uin)
        if values or name:
            self.store.save_player(uin, name=name, **values)
            player, _ = self.store.get_or_create_player(uin)
        player["plants"] = self.store.get_player_plants(uin)
        return player

    def invoke(self, suffix, data, player):
        name = "PVZ.Cmd.Cmd_" + suffix + "_CS"
        if name not in self.game.REQUEST_HANDLERS:
            raise AssertionError("Missing explicit route: " + name)
        result = self.game.REQUEST_HANDLERS[name](data, player)
        return result if isinstance(result, list) else [(name[:-3] + "_SC", result)]

    def train_owned_cards(self, player, level=100):
        """Upgrade only this disposable account's real cards for winning fixtures."""
        import sqlite3
        with closing(sqlite3.connect(self.store.DB_PATH)) as con, con:
            con.execute('UPDATE cards SET level=? WHERE uin=?', (level, player['uin']))

    def close(self):
        self.store.close_thread_db()
        for handler in list(logging.getLogger().handlers):
            if handler not in self._saved_log_handlers:
                logging.getLogger().removeHandler(handler)
                handler.close()
        logging.getLogger().setLevel(self._saved_log_level)
        for handler in self._saved_log_handlers:
            if handler not in logging.getLogger().handlers:
                logging.getLogger().addHandler(handler)
        if str(self.server_dir) in sys.path:
            sys.path.remove(str(self.server_dir))
        for name in self.sources:
            sys.modules.pop(name, None)
        if self.temporary:
            # SQLite connection objects retained in expected exception cycles
            # must release their Windows file handles before fixture deletion.
            gc.collect()
            self.temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def fields(data):
    """Independent strict protobuf decoder preserving repeated and nested values."""
    out = {}
    position = 0
    def varint():
        nonlocal position
        value = shift = 0
        while True:
            if position >= len(data) or shift > 63:
                raise ValueError("Truncated or excessive protobuf varint")
            byte = data[position]
            position += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
            shift += 7
    while position < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("Zero protobuf field")
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if position + length > len(data):
                raise ValueError("Truncated protobuf field")
            value = data[position:position + length]
            position += length
        else:
            raise ValueError("Unsupported protobuf wire type " + str(wire))
        out.setdefault(number, []).append((wire, value))
    return out


def one(data, number, default=None):
    values = fields(data).get(number)
    return values[-1][1] if values else default


def many(data, number):
    return [value for _, value in fields(data).get(number, [])]


def message(messages, name):
    suffix = ".Cmd_" + name + "_SC"
    return next(body for kind, body in messages if kind.endswith(suffix))
