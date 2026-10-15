#!/usr/bin/env python3
"""
Logging static web server for the PvZ Online client.

Purpose: serve the real client over HTTP and PRINT EVERY REQUEST with its status
code, so we can see exactly which files the loader asks for and which ones 404.
That tells us why the loader is stuck on the loading screen (it's an asset-path
problem, not a server problem).

Run:
    python serve_client.py
Then in the standalone Flash projector:  File > Open... > URL:
    http://127.0.0.1:8080/loader_n.swf
(or later, the full page:  http://127.0.0.1:8080/game.html )

Watch this console. A burst of 200s ending in a 404 usually pinpoints the file the
loader is waiting on. Loading over http (not a local file) also puts the SWF in the
"remote" security sandbox, which is friendlier for the socket connection later.
"""

import http.server
import socketserver
import os
import sys
import base64
import re
import threading
from urllib.parse import unquote, urlsplit

# 1x1 fully-transparent RGBA PNG — returned for any missing image so the client gets a
# valid (blank) bitmap instead of a 404. Kills the ioError storm + tight retry loops that
# missing loose PNGs (chat/friend/roleInfo backgrounds, head icons…) cause.
#
# WARNING: do not hand-edit this blob. Every chunk's declared length must match its real
# byte count and its CRC32 must be valid, otherwise Flash raises
#   ArgumentError: Error #2015: 无效的 BitmapData
# at BitmapData/copyPixels() (via asgui.blit.data::BlitData/SetContent) the moment the
# loader hands the bytes over — which surfaces as a crash inside an unrelated-looking
# network callback stack. tests/test_placeholder_png.py re-parses the PNG and asserts the
# signature, chunk lengths, CRCs, zlib stream and pixel alpha, so run it after any edit.
PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII=")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif")
CARD_ICON_RE = re.compile(r"^/swf/icons/card/card_icon_(\d+)(?:_[0-9a-f]+)?\.swf$", re.IGNORECASE)
CARD_IMAGE_RE = re.compile(r"^/swf/icons/card/(\d+)_(\d+)\.png$", re.IGNORECASE)
# Bag/道具 icons. The shipped pack only carries a slice of the item ids (for example just 6 of the
# 955xx puppet items and 2 of the 956xx zombie fragments), so a capture drop whose icon is missing
# used to be answered with the 1x1 transparent placeholder — the player saw an EMPTY reward slot
# ("抽到空气"). Reuse the nearest real icon of the same item family instead.
ITEM_ICON_RE = re.compile(r"^/image/item/(\d+)\.png$", re.IGNORECASE)

# Prefer the assembled ../webroot (built by build_webroot.py); fall back to the raw
# "PvZ Online Folder" if the webroot hasn't been built yet.
HERE = os.path.dirname(os.path.abspath(__file__))
_WEBROOT = os.path.abspath(os.path.join(HERE, "..", "webroot"))
_RAW = os.path.abspath(os.path.join(HERE, "..", "PvZ Online Folder"))
CLIENT_DIR = _WEBROOT if os.path.isdir(_WEBROOT) else _RAW
PORT = 8080
ASSET_ERROR_LOG = os.path.join(HERE, "client_asset_errors.log")
_ASSET_ERROR_LOCK = threading.Lock()

EXTRA_MIME = {
    ".swf": "application/x-shockwave-flash",
    ".xml": "text/xml",
    ".bin": "application/octet-stream",
    ".json": "application/json",
}

_CARD_ICON_ALIASES = None


def _load_card_icon_aliases():
    """Map battleCard ids to their configured icon ids.

    The clientDB uses e.g. card id 1205 -> icon 30, while older/local client
    requests can ask for ``card_icon_1205.swf``.  The webroot contains the
    canonical ``card_icon_30.swf`` resource, so serve the configured resource
    instead of a transparent placeholder.
    """
    global _CARD_ICON_ALIASES
    if _CARD_ICON_ALIASES is not None:
        return _CARD_ICON_ALIASES
    _CARD_ICON_ALIASES = {}
    archive = os.path.join(CLIENT_DIR, "clientDB.txt")
    try:
        data = open(archive, "rb").read()
        pos = 0
        while pos + 4 <= len(data):
            name_len = int.from_bytes(data[pos:pos + 4], "big")
            pos += 4
            name = data[pos:pos + name_len].decode("utf-8", "replace")
            pos += name_len
            size = int.from_bytes(data[pos:pos + 4], "big")
            pos += 4
            chunk = data[pos:pos + size]
            pos += size
            if name != "battleCard.txt":
                continue
            rows = [line.split("\t") for line in chunk.decode("utf-8", "replace").replace("\r", "").split("\n")
                    if line.strip()]
            if not rows:
                break
            header = {value: index for index, value in enumerate(rows[0])}
            id_index, icon_index = header.get("id"), header.get("icon")
            if id_index is None or icon_index is None:
                break
            for row in rows[1:]:
                try:
                    card_id, icon_id = int(row[id_index]), int(row[icon_index])
                except (IndexError, ValueError):
                    continue
                if card_id > 0 and icon_id > 0:
                    _CARD_ICON_ALIASES[card_id] = icon_id
            break
    except (OSError, ValueError):
        pass
    sys.stdout.write("  loaded %d card icon aliases from clientDB\n" % len(_CARD_ICON_ALIASES))
    sys.stdout.flush()
    return _CARD_ICON_ALIASES


_FRAGMENT_ICON_ALIASES = None


def _load_fragment_icon_aliases():
    """Missing plant fragments use art of their authored plant, never a nearby ID.

    Fragment IDs are independent of TD plant IDs. The explicit recycleItemString
    and ItemInfo name establish identity; battleCard.icon chooses the plant art.
    Values of None still mark fragments so unrelated generic fallback is skipped.
    """
    global _FRAGMENT_ICON_ALIASES
    if _FRAGMENT_ICON_ALIASES is not None:
        return _FRAGMENT_ICON_ALIASES
    tables = {}
    try:
        with open(os.path.join(CLIENT_DIR, "clientDB.txt"), "rb") as archive:
            data = archive.read()
        pos = 0
        while pos + 4 <= len(data):
            name_len = int.from_bytes(data[pos:pos + 4], "big")
            pos += 4
            name = data[pos:pos + name_len].decode("utf-8")
            pos += name_len
            size = int.from_bytes(data[pos:pos + 4], "big")
            pos += 4
            chunk = data[pos:pos + size]
            pos += size
            if name not in ("ItemInfo.txt", "battleCard.txt"):
                continue
            rows = [line.split("\t") for line in chunk.decode("utf-8-sig").splitlines()
                    if line.strip()]
            if rows:
                tables[name] = [dict(zip(rows[0], row)) for row in rows[1:]]
        fragments = {}
        for item in tables.get("ItemInfo.txt", []):
            if (item.get("type") == "5" and item.get("subType") == "501"
                    and item.get("name", "").startswith("碎片-")):
                iid, icon = int(item["id"]), int(item["icon"])
                fragments[iid] = item
        candidates = {}
        identity_icons = {}
        for card in tables.get("battleCard.txt", []):
            fragment = int((card.get("recycleItemString") or "0,0").split(",")[0])
            item = fragments.get(fragment)
            if not item or item.get("name") != "碎片-" + card.get("name", ""):
                continue
            card_id, icon = int(card["id"]), int(card["icon"])
            identity_icons.setdefault(fragment, set()).add(icon)
            target = "/swf/icons/card/%d_%d.png" % (icon, card_id)
            if os.path.isfile(os.path.join(CLIENT_DIR, target.lstrip("/"))):
                candidates.setdefault(fragment, []).append((int(card["star"]), card_id, target))
        # Some original card PNGs carry a legacy card suffix (e.g. 125_251).
        # Their icon prefix is still the authored plant icon, so identity holds.
        card_dir = os.path.join(CLIENT_DIR, "swf", "icons", "card")
        for filename in sorted(os.listdir(card_dir)):
            match = re.fullmatch(r"(\d+)_(\d+)\.png", filename, re.IGNORECASE)
            if not match:
                continue
            icon, suffix = map(int, match.groups())
            for fragment, icons in identity_icons.items():
                if not candidates.get(fragment) and icon in icons:
                    candidates[fragment] = [(0, suffix, "/swf/icons/card/" + filename)]
        _FRAGMENT_ICON_ALIASES = {
            int(item["icon"]): min(candidates[iid])[2] if candidates.get(iid) else None
            for iid, item in fragments.items()
        }
    except (OSError, ValueError, KeyError, UnicodeError) as error:
        sys.stdout.write("  FRAGMENT ICON CONFIG ERROR %s\n" % error)
        _FRAGMENT_ICON_ALIASES = {}
    return _FRAGMENT_ICON_ALIASES


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=CLIENT_DIR, **kwargs)

    def _record_asset_error(self, status, path):
        """Persist missing client assets so an in-game blank sprite can be traced to its URL."""
        try:
            with _ASSET_ERROR_LOCK, open(ASSET_ERROR_LOG, "a", encoding="utf-8") as log_file:
                log_file.write("%s %s %s\n" % (self.log_date_time_string(), status, path))
        except OSError:
            pass

    def do_GET(self):
        request_path = unquote(urlsplit(self.path).path)
        requested_path = self.path
        # Keep canonical files untouched.  Only repair a missing card-id URL by
        # resolving its battleCard.icon resource from clientDB.
        if not os.path.exists(self.translate_path(self.path)):
            image_match = CARD_IMAGE_RE.match(request_path)
            if image_match:
                icon_id, card_id = map(int, image_match.groups())
                card_dir = os.path.dirname(self.translate_path(self.path))
                # The shipped pack has base-card PNGs for many icon IDs but omits
                # their upgraded-card variants (for example 30_1202.png while
                # 30_1201.png exists). Reuse the nearest real image for the SAME
                # plant icon so the card art stays visible; never serve another
                # plant's art merely to fill the frame.
                same_icon = []
                try:
                    for filename in os.listdir(card_dir):
                        candidate = re.fullmatch(r"%d_(\d+)\.png" % icon_id, filename, re.IGNORECASE)
                        if candidate:
                            target_id = int(candidate.group(1))
                            same_icon.append((abs(target_id - card_id), target_id, filename))
                except OSError:
                    pass
                if same_icon:
                    _, _, filename = min(same_icon)
                    query = urlsplit(self.path).query
                    self.path = "/swf/icons/card/" + filename + (("?" + query) if query else "")
                    sys.stdout.write("  CARD IMAGE FALLBACK %s -> %s\n" % (requested_path, self.path))
                    sys.stdout.flush()
            match = CARD_ICON_RE.match(request_path)
            if match and not os.path.exists(self.translate_path(self.path)):
                card_id = int(match.group(1))
                icon_id = _load_card_icon_aliases().get(card_id)
                target = "/swf/icons/card/card_icon_%d.swf" % icon_id if icon_id else ""
                if target and os.path.isfile(self.translate_path(target)):
                    original = self.path
                    query = urlsplit(self.path).query
                    self.path = target + (("?" + query) if query else "")
                    sys.stdout.write("  CARD ICON ALIAS %s -> %s\n" % (original, self.path))
                    sys.stdout.flush()
            item_match = ITEM_ICON_RE.match(request_path)
            if item_match and not os.path.exists(self.translate_path(self.path)):
                item_id = int(item_match.group(1))
                fragments = _load_fragment_icon_aliases()
                query = urlsplit(self.path).query
                if item_id in fragments:
                    target = fragments[item_id]
                    if target:
                        self.path = target + (("?" + query) if query else "")
                        sys.stdout.write("  PLANT FRAGMENT ICON %s -> %s\n" % (requested_path, self.path))
                        sys.stdout.flush()
                    # A fragment without matching plant art must never show another
                    # plant's materials. The normal placeholder path handles it.
                else:
                    # Retain generic item fallback for non-plant-fragment assets.
                    item_dir = os.path.dirname(self.translate_path(self.path))
                    nearest = []
                    try:
                        for filename in os.listdir(item_dir):
                            candidate = re.fullmatch(r"(\d+)\.png", filename, re.IGNORECASE)
                            if not candidate:
                                continue
                            other = int(candidate.group(1))
                            same_family = other // 100 == item_id // 100
                            nearest.append((0 if same_family else 1, abs(other - item_id),
                                            other, filename))
                    except OSError:
                        pass
                    if nearest:
                        _, _, _, filename = min(nearest)
                        self.path = "/image/item/" + filename + (("?" + query) if query else "")
                        sys.stdout.write("  ITEM ICON FALLBACK %s -> %s\n" % (requested_path, self.path))
                        sys.stdout.flush()
        # Any stale hashed ModuleHole URL must resolve to the canonical SWF.
        # Older bad builds left VerifyError #1023 classes in Flash cache; serving
        # those hash-named files again can resurrect the crash.
        module_hole_match = re.match(r"^/ModuleHole_[0-9a-f]{32}\.swf$", request_path, re.I)
        if module_hole_match:
            query = urlsplit(self.path).query
            self.path = "/ModuleHole.swf" + (("?" + query) if query else "")
            sys.stdout.write("  MODULEHOLE ALIAS %s -> %s\n" % (requested_path, self.path))
            sys.stdout.flush()
        # Missing image? hand back a 1x1 transparent PNG instead of 404.
        path = self.translate_path(self.path)
        if not os.path.exists(path) and path.lower().endswith(IMAGE_EXTS):
            sys.stdout.write("  ASSET FALLBACK %s -> 1x1 placeholder (source missing)\n" % self.path)
            sys.stdout.flush()
            self._record_asset_error("IMAGE_FALLBACK", self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PLACEHOLDER_PNG)))
            self.end_headers()
            self.wfile.write(PLACEHOLDER_PNG)
            return
        return super().do_GET()

    def send_error(self, code, message=None, explain=None):
        if code == 404:
            self._record_asset_error("404", self.path)
        return super().send_error(code, message, explain)

    def guess_type(self, path):
        for ext, mime in EXTRA_MIME.items():
            if path.lower().endswith(ext):
                return mime
        return super().guess_type(path)

    def end_headers(self):
        # never cache — we want to see fresh requests every run
        self.send_header("Cache-Control", "no-store")
        # Flash sometimes wants a crossdomain allowance for data loads
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Colour-free, compact: "GET /conf/init.xml -> 404"
        try:
            code = int(str(args[1]))
        except (IndexError, ValueError):
            code = 0
        mark = "  <<< NOT FOUND" if code == 404 else ""
        sys.stdout.write("  %s -> %s%s\n" % (self.requestline, str(args[1]), mark))
        sys.stdout.flush()


class Server(socketserver.ThreadingTCPServer):
    # IMPORTANT: on Windows, allow_reuse_address=True lets a SECOND process bind the
    # same port while the OLD one keeps getting the traffic. Keep it False so a stale
    # instance makes us fail loudly instead of silently doing nothing.
    allow_reuse_address = False
    daemon_threads = True


def main():
    # Patch player-facing SWF strings before binding the HTTP port. This also
    # covers users who start serve_client.py directly instead of using run_all.py.
    try:
        from client_tools.translate_battle_ui import apply_translation
        apply_translation()
    except Exception as exc:
        # Keep the local game usable even if an asset is incompatible with the patcher.
        print("[battle-ui-translation] Could not apply patch: %r" % exc)

    if not os.path.isdir(CLIENT_DIR):
        print("ERROR: client folder not found at:\n  %s" % CLIENT_DIR)
        sys.exit(1)
    which = "webroot (assembled)" if CLIENT_DIR == _WEBROOT else "PvZ Online Folder (RAW - run build_webroot.py first!)"
    print("=" * 60)
    print("Serving: %s" % which)
    print("  %s" % CLIENT_DIR)
    print("Verify in a browser:")
    print("  http://127.0.0.1:%d/swf/resource_64c342244c3d8f16b3c815e5ee133cf8.swf" % PORT)
    print("  (should download a ~286 KB file AND print a 200 line below)")
    print("Load in the Flash projector:")
    print("  http://127.0.0.1:%d/loader_n.swf" % PORT)
    print("=" * 60)
    print("(Ctrl+C to stop)\n")
    try:
        httpd = Server(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print("\n!!! Could NOT bind port %d: %s" % (PORT, e))
        print("!!! Another server (probably an old serve_client) is already using it.")
        print("!!! Kill it first:  netstat -ano | findstr :%d   then   taskkill /F /PID <pid>" % PORT)
        sys.exit(1)
    with httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopping")


if __name__ == "__main__":
    main()
