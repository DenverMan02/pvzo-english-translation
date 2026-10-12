"""serve_client.PLACEHOLDER_PNG must be a byte-valid 1x1 PNG.

Every image the client asks for that is not on disk is answered with this blob instead of a
404 (see serve_client.do_GET -> _record_asset_error("IMAGE_FALLBACK", ...)). So a single
corrupted byte here breaks EVERY panel that draws a missing loose PNG — the symptom is

    ArgumentError: Error #2015: 无效的 BitmapData
      at flash.display::BitmapData/copyPixels()
      at asgui.blit.data::BlitData/SetContent()
      at com.qq.modules.main.server::PvzSocketService/onReceived()

which surfaces inside a network callback and looks nothing like an image bug.

The blob that shipped with that bug declared its IDAT chunk length as 11 while carrying 13
bytes, so zlib.decompress raised "incomplete or truncated stream" and the CRC never matched.
This test re-parses the PNG from scratch and asserts signature, per-chunk lengths, every
CRC32, and that the IDAT inflates to exactly one scanline of 1x1 RGBA.
"""
import base64
import importlib.util
import os
import struct
import unittest
import zlib

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG_PATH = os.path.join(SERVER, "serve_client.py")

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def load_placeholder():
    """Import serve_client.py without starting its threads."""
    spec = importlib.util.spec_from_file_location("_serve_client_probe", PNG_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PLACEHOLDER_PNG


def chunks(blob):
    """[(type, data, stored_crc, trailing_bytes)] walking the PNG chunk stream."""
    out, pos = [], 8
    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        ctype = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        end = pos + 8 + length
        stored = struct.unpack(">I", blob[end:end + 4])[0] if end + 4 <= len(blob) else None
        out.append((ctype, data, stored, len(blob) - end - 4 if stored is None else 0))
        if stored is None:
            break
        pos = end + 4
    return out


class PlaceholderPngTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = load_placeholder()
        cls.parsed = chunks(cls.blob)

    def test_signature(self):
        self.assertEqual(self.blob[:8], PNG_SIG, "not a PNG — the client will reject it")

    def test_chunk_stream_is_well_formed(self):
        """Every declared length must fit inside the blob and be followed by a CRC."""
        for ctype, data, stored, trailing in self.parsed:
            self.assertIsNotNone(stored, "chunk %r runs past the end of the blob" % ctype)
            self.assertEqual(trailing, 0, "chunk %r has trailing garbage" % ctype)

    def test_every_chunk_crc_matches(self):
        for ctype, data, stored, _ in self.parsed:
            self.assertEqual(stored, zlib.crc32(ctype + data) & 0xFFFFFFFF,
                             "CRC mismatch on chunk %r — this is the #2015 bug" % ctype)

    def test_ihdr_is_1x1_rgba(self):
        head = [c for c in self.parsed if c[0] == b"IHDR"]
        self.assertEqual(len(head), 1, "missing IHDR")
        width, height, depth, color = struct.unpack(">IIBB", head[0][1][:10])
        self.assertEqual((width, height), (1, 1))
        self.assertEqual((depth, color), (8, 6), "expected 8-bit RGBA")

    def test_idat_inflates_to_one_transparent_pixel(self):
        """The actual #2015 trigger: a truncated/oversized IDAT makes zlib fail, and Flash
        surfaces that as 'invalid BitmapData' inside BlitData/SetContent."""
        idat = b"".join(c[1] for c in self.parsed if c[0] == b"IDAT")
        self.assertTrue(idat, "no IDAT chunk")
        try:
            raw = zlib.decompress(idat)
        except zlib.error as exc:
            self.fail("IDAT does not inflate (%s) — this is the #2015 bug" % exc)
        # 1 filter byte + 1 pixel of RGBA
        self.assertEqual(len(raw), 5, "expected 5 bytes of scanline data, got %d" % len(raw))
        self.assertIn(raw[0], (0, 1), "unknown PNG filter type %d" % raw[0])
        # Filter 0 (None) stores the pixel literally; filter 1 (Sub) stores deltas against
        # the pixel to the left, which for a single-pixel row means byte i is added to the
        # running total, starting at 0.
        if raw[0] == 0:
            rgba = raw[1:]
        else:
            acc, rgba = 0, bytearray()
            for byte in raw[1:]:
                acc = (acc + byte) & 0xFF
                rgba.append(acc)
        self.assertEqual(len(rgba), 4, "expected RGBA")
        self.assertEqual(rgba[3], 0x00,
                         "alpha must be 0 — the placeholder stands in for a blank/empty "
                         "asset, so anything opaque shows up as a black 1x1 square")

    def test_ends_with_iend(self):
        self.assertEqual(self.parsed[-1][0], b"IEND", "PNG must terminate with IEND")

    def test_idat_declared_length_matches_its_bytes(self):
        """The specific regression: IDAT declared 11 but carried 13 bytes."""
        for ctype, data, stored, _ in self.parsed:
            if ctype == b"IDAT":
                # data length comes from the chunk header, so re-read it explicitly.
                pos = 8
                while pos + 8 <= len(self.blob):
                    (length,) = struct.unpack(">I", self.blob[pos:pos + 4])
                    if self.blob[pos + 4:pos + 8] == b"IDAT":
                        self.assertEqual(length, len(data),
                                         "IDAT length header says %d but %d bytes follow"
                                         % (length, len(data)))
                    pos += 12 + length


if __name__ == "__main__":
    unittest.main()
