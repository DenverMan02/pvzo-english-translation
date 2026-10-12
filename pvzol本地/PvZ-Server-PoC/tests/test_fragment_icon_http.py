"""Verify plant fragments never display another plant's numeric-neighbour icon.

Exercise the actual HTTP handler against every upgrade fragment. The 24 absent
item icons have independently checked, same-plant card PNG sources; the 54
original item icons must remain byte-for-byte intact. No player DB is opened.
"""
from pathlib import Path
import csv
import io
import hashlib
import json
import struct
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen
from PIL import Image, ImageOps

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))
import serve_client


EXPECTED_MISSING_SOURCES = {
    54300: "550_5501.png", 56200: "770_7701.png",
    56300: "780_7801.png", 56400: "850_8501.png",
    56600: "860_8601.png", 56800: "830_8301.png",
    57000: "810_8101.png", 57600: "900_9001.png",
    57700: "890_8901.png", 57800: "910_9101.png",
    57900: "920_9201.png", 58100: "940_9401.png",
    58200: "950_9501.png", 58500: "980_9801.png",
    58700: "112_1121.png", 58800: "113_1131.png",
    58900: "114_1141.png", 59200: "117_1171.png",
    59300: "118_1181.png", 59400: "122_1221.png",
    59500: "119_1191.png", 59600: "123_1231.png",
    59700: "124_1241.png", 59800: "125_251.png",
}


def read_tables(path):
    data = path.read_bytes()
    result, offset = {}, 0
    while offset < len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        offset += 4
        name = data[offset:offset + size].decode("utf-8")
        offset += size
        size = int.from_bytes(data[offset:offset + 4], "big")
        offset += 4
        rows = data[offset:offset + size].decode("utf-8")
        offset += size
        result[name] = list(csv.DictReader(io.StringIO(rows), delimiter="\t"))
    return result


def archive(tables):
    result = bytearray()
    for name, text in tables.items():
        name_bytes, body = name.encode(), text.encode()
        result.extend(struct.pack(">I", len(name_bytes)) + name_bytes)
        result.extend(struct.pack(">I", len(body)) + body)
    return result


class QuietHandler(serve_client.Handler):
    def log_message(self, *args):
        pass


class FragmentIconHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.webroot = Path(serve_client.CLIENT_DIR)
        cls.temp = tempfile.TemporaryDirectory(prefix="pvz-fragment-http-")
        cls.saved_error_log = serve_client.ASSET_ERROR_LOG
        cls.saved_aliases = serve_client._FRAGMENT_ICON_ALIASES
        serve_client.ASSET_ERROR_LOG = str(Path(cls.temp.name) / "asset-errors.log")
        serve_client._FRAGMENT_ICON_ALIASES = None
        cls.tables = read_tables(cls.webroot / "clientDB.txt")
        cls.manifest = json.loads((cls.webroot / "plant-fragment-icon-repair-manifest.json").read_text(encoding="utf-8"))
        cls.fragments = {
            int(row["icon"]): row for row in cls.tables["ItemInfo.txt"]
            if row["type"] == "5" and row["subType"] == "501"
            and row["name"].startswith("碎片-")
        }
        cls.httpd = serve_client.Server(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:%d" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        serve_client.ASSET_ERROR_LOG = cls.saved_error_log
        serve_client._FRAGMENT_ICON_ALIASES = cls.saved_aliases
        cls.temp.cleanup()

    def get(self, path):
        with urlopen(self.url + path, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            return response.read()

    def test_all_upgrade_fragment_families_are_covered(self):
        ids = {int(row["id"]) for row in self.fragments.values()}
        costs = {int(row["costItemID"]) for row in self.tables["CardUpgradeTemplate.txt"]}
        self.assertEqual(ids, costs)
        self.assertEqual(len(costs), 78)
        aliases = serve_client._load_fragment_icon_aliases()
        self.assertEqual(set(aliases), set(self.fragments))

    def test_every_original_item_icon_is_preserved(self):
        hashes = self.manifest["preserved_original_sha256"]
        self.assertEqual(len(hashes), 54)
        for relative, digest in hashes.items():
            path = self.webroot / relative
            with self.subTest(icon=relative):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
                self.assertEqual(self.get("/" + relative), path.read_bytes())

    def test_all_recovered_icons_are_complete_native_plant_images(self):
        missing = {
            icon for icon in self.fragments
            if not (self.webroot / "image" / "item" / ("%d.png" % icon)).is_file()
        }
        self.assertEqual(missing, set())
        records = {row["item_id"]: row for row in self.manifest["records"]}
        self.assertEqual(set(records), set(EXPECTED_MISSING_SOURCES))
        for icon, source in sorted(EXPECTED_MISSING_SOURCES.items()):
            with self.subTest(icon=icon, plant=self.fragments[icon]["name"]):
                record = records[icon]
                self.assertEqual(record["native_source"], "swf/icons/card/" + source)
                source_path = self.webroot / record["native_source"]
                output = self.webroot / ("image/item/%d.png" % icon)
                self.assertFalse(record["was_existing"])
                self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), record["native_source_sha256"])
                self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), record["output_sha256"])
                native = Image.open(source_path).convert("RGBA")
                cropped = native.crop(native.getchannel("A").getbbox())
                subject = ImageOps.contain(cropped, (46, 46), Image.Resampling.LANCZOS)
                expected = Image.new("RGBA", (50, 50))
                expected.alpha_composite(subject, ((50 - subject.width) // 2, (50 - subject.height) // 2))
                actual = Image.open(output)
                self.assertEqual(actual.size, (50, 50))
                self.assertEqual(actual.mode, "RGBA")
                self.assertEqual(actual.tobytes(), expected.tobytes())
                bounds = actual.getchannel("A").getbbox()
                self.assertTrue(bounds and bounds[0] >= 2 and bounds[1] >= 2 and bounds[2] <= 48 and bounds[3] <= 48)
                self.assertEqual(self.get("/image/item/%d.png" % icon), output.read_bytes())

    def test_missing_icons_still_resolve_to_the_same_plant(self):
        # Simulate a resource pack lacking the recovered PNGs: only native card
        # portraits are present, so all 24 routes exercise the HTTP alias branch.
        with tempfile.TemporaryDirectory(prefix="pvz-missing-fragments-") as temp:
            fixture = Path(temp)
            (fixture / "swf/icons/card").mkdir(parents=True)
            (fixture / "clientDB.txt").write_bytes((self.webroot / "clientDB.txt").read_bytes())
            for source in EXPECTED_MISSING_SOURCES.values():
                (fixture / "swf/icons/card" / source).write_bytes((self.webroot / "swf/icons/card" / source).read_bytes())
            saved_dir = serve_client.CLIENT_DIR
            saved_aliases = serve_client._FRAGMENT_ICON_ALIASES
            try:
                serve_client.CLIENT_DIR = str(fixture)
                serve_client._FRAGMENT_ICON_ALIASES = None
                aliases = serve_client._load_fragment_icon_aliases()
                for icon, source in sorted(EXPECTED_MISSING_SOURCES.items()):
                    with self.subTest(icon=icon):
                        expected_path = "/swf/icons/card/" + source
                        self.assertEqual(aliases[icon], expected_path)
                        self.assertEqual(self.get("/image/item/%d.png" % icon), (fixture / expected_path.lstrip("/")).read_bytes())
                self.assertEqual(self.get("/image/item/59300.png?reload=fragment-repair"), (fixture / "swf/icons/card/118_1181.png").read_bytes())
                self.assertEqual(self.get("/image/item/59800.png?reload=fragment-repair"), (fixture / "swf/icons/card/125_251.png").read_bytes())
            finally:
                serve_client.CLIENT_DIR = saved_dir
                serve_client._FRAGMENT_ICON_ALIASES = saved_aliases

    def test_reported_redstinger_and_query_string(self):
        expected = (self.webroot / "image/item/59300.png").read_bytes()
        wrong = (self.webroot / "image/item/59100.png").read_bytes()
        actual = self.get("/image/item/59300.png?reload=fragment-repair")
        self.assertEqual(actual, expected)
        self.assertNotEqual(actual, wrong)
        native = self.webroot / "image/item/51100.png"
        self.assertEqual(self.get("/image/item/51100.png?reload=fragment-repair"), native.read_bytes())

    def test_unmatched_fragment_never_borrows_another_plant(self):
        # A declared fragment without an exact named card must stay unresolved.
        # The neighbouring item exists, reproducing the former wrong fallback.
        with tempfile.TemporaryDirectory(prefix="pvz-unmatched-fragment-") as temp:
            fixture = Path(temp)
            (fixture / "image/item").mkdir(parents=True)
            (fixture / "swf/icons/card").mkdir(parents=True)
            wrong = (self.webroot / "image/item/59100.png").read_bytes()
            (fixture / "image/item/59100.png").write_bytes(wrong)
            (fixture / "swf/icons/card/116_1161.png").write_bytes(wrong)
            (fixture / "clientDB.txt").write_bytes(archive({
                "ItemInfo.txt": "id\tname\ticon\ttype\tsubType\r\n59300\t碎片-红针花\t59300\t5\t501\r\n",
                "battleCard.txt": "id\tname\ticon\tstar\trecycleItemString\r\n1161\t金蟾菇\t116\t1\t59300,20\r\n",
            }))
            saved_dir = serve_client.CLIENT_DIR
            saved_aliases = serve_client._FRAGMENT_ICON_ALIASES
            try:
                serve_client.CLIENT_DIR = str(fixture)
                serve_client._FRAGMENT_ICON_ALIASES = None
                self.assertIsNone(serve_client._load_fragment_icon_aliases()[59300])
                actual = self.get("/image/item/59300.png")
                self.assertEqual(actual, serve_client.PLACEHOLDER_PNG)
                self.assertNotEqual(actual, wrong)
            finally:
                serve_client.CLIENT_DIR = saved_dir
                serve_client._FRAGMENT_ICON_ALIASES = saved_aliases


if __name__ == "__main__":
    unittest.main()
