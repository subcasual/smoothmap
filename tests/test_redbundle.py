"""Tests for redbundle (reading bundles, finding files in mods) and lz4block, on small bundles built
here: no game files needed."""
import os
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import lz4block                                                 # noqa: E402
from redbundle import Bundle, find_file, find_files            # noqa: E402
from redbundle.bundles import LZ4, STORED, ZLIB                 # noqa: E402

# "abcd"; a match 4 back, 8 long (overlapping its own output); then 6 literals (a block must end with
# at least 5, and its last match start at least 12 bytes before the end: python-lz4 checks)
LZ4_BLOCK, LZ4_DATA = bytes([0x44]) + b"abcd" + bytes([4, 0, 0x60]) + b"!?!?!?", b"abcdabcdabcd!?!?!?"
MAP = "gameplay\\gui_new\\swf\\worldmap\\panel_worldmap.redswf"


def make_bundle(files):
    """A bundle in REDkit's 0x130-entry layout from (depot path, data, compression, stored bytes)."""
    table_size = 0x130 * len(files)
    offset, table, body = 0x20 + table_size, b"", b""
    for name, data, compression, stored in files:
        table += (name.encode().ljust(0x100, b"\0") + b"\0" * 16
                  + struct.pack("<IIIIII", offset, 0, len(data), len(stored), zlib.crc32(data), compression)
                  + b"\0" * 8)
        body += stored
        offset += len(stored)
    total = 0x20 + table_size + len(body)
    return b"POTATO70" + struct.pack("<III", (total + 15) // 16 * 16, 0, table_size) + b"\0" * 12 + table + body


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class Lz4(unittest.TestCase):
    def test_builtin_decoder(self):
        self.assertEqual(lz4block.decompress_builtin(LZ4_BLOCK, len(LZ4_DATA)), LZ4_DATA)
        with self.assertRaises(ValueError):
            lz4block.decompress_builtin(LZ4_BLOCK, len(LZ4_DATA) + 1)

    @unittest.skipUnless(lz4block._lz4_block, "lz4 package not installed")
    def test_decoders_agree(self):
        data = b"".join(b"%d smooth map " % (i % 97) for i in range(5000)) + bytes(range(256)) * 20
        block = lz4block._lz4_block.compress(data, store_size=False)
        for name, decode in lz4block.DECOMPRESSORS.items():
            self.assertEqual(decode(block, len(data)), data, name)
            with self.assertRaises(ValueError):
                decode(block, len(data) + 1)

    def test_choice_by_name(self):
        self.assertIs(lz4block.decompressor("builtin"), lz4block.decompress_builtin)
        with self.assertRaises(ValueError):
            lz4block.decompressor("magic")


class Bundles(unittest.TestCase):
    FILES = [("a\\stored.txt", b"plain", STORED, b"plain"),
             ("a\\zlib.txt", b"squeezed " * 20, ZLIB, zlib.compress(b"squeezed " * 20)),
             ("b\\Lz4.txt", LZ4_DATA, LZ4, LZ4_BLOCK)]

    def test_reads_every_compression(self):
        for lz4 in (lz4block.DEFAULT, lz4block.decompress_builtin):
            bundle = Bundle(make_bundle(self.FILES), lz4)
            self.assertEqual(bundle.entry_size, 0x130)
            for name, data, _, _ in self.FILES:
                self.assertEqual(bundle.read(bundle.find(name)), data)

    def test_find_ignores_case_and_slashes(self):
        bundle = Bundle(make_bundle(self.FILES))
        self.assertEqual(bundle.find("B/lz4.TXT").name, "b\\Lz4.txt")
        self.assertIsNone(bundle.find("b\\missing.txt"))

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "not a REDengine 3 bundle"):
            Bundle(b"PK\3\4" + b"\0" * 60)
        broken = bytearray(make_bundle(self.FILES[:1]))
        broken[-1] ^= 1                                          # the stored file's last byte
        bundle = Bundle(bytes(broken))
        with self.assertRaisesRegex(ValueError, "CRC mismatch"):
            bundle.read(bundle.entries[0])


class FindingFiles(unittest.TestCase):
    def setUp(self):
        self.mod = tempfile.mkdtemp()
        write(os.path.join(self.mod, "content", "blob0.bundle"),
              make_bundle([(MAP, b"bundled", STORED, b"bundled")]))

    def test_one_copy(self):
        data, where = find_file(self.mod, MAP)
        self.assertEqual(data, b"bundled")
        self.assertTrue(where.endswith(f"blob0.bundle :: {MAP}"), where)
        self.assertEqual(find_file(self.mod, MAP.replace("\\", "/"))[0], b"bundled")
        self.assertEqual(find_file(os.path.join(self.mod, "content", "blob0.bundle"), MAP)[0], b"bundled")

    def test_loose_copy_too(self):
        write(os.path.join(self.mod, "content", "panel_worldmap.redswf"), b"loose")
        self.assertEqual(sorted(data for data, _ in find_files(self.mod, MAP)), [b"bundled", b"loose"])
        with self.assertRaisesRegex(LookupError, "more than one"):
            find_file(self.mod, MAP)

    def test_missing(self):
        with self.assertRaisesRegex(LookupError, "no hud.redswf found"):
            find_file(self.mod, "gameplay\\hud.redswf")

    def test_unbundler_is_swappable(self):
        calls = []

        def unbundle(bundle_path, depot_path):
            calls.append((os.path.basename(bundle_path), depot_path))
            return b"from elsewhere"

        self.assertEqual(find_file(self.mod, MAP, unbundle)[0], b"from elsewhere")
        self.assertEqual(calls, [("blob0.bundle", MAP)])


if __name__ == "__main__":
    unittest.main()
