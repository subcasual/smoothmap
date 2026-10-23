"""Reading REDengine 3 bundles (blob0.bundle etc.), as found in published mods.

Layout: "POTATO70", u32 total size (file length rounded up to 16), u32 0, u32 table size, then one
table entry per file: name[0x100], hash[16], then the fields below. Two entry layouts exist,
written by different tools:

    0x130 bytes (REDkit; e.g. Map Remastered):      +0x110 offset, +0x118 size, +0x11C stored size,
                                                    +0x120 crc32, +0x124 compression
    0x140 bytes (other packers; e.g. Smooth Menus): +0x114 size, +0x118 stored size, +0x11C offset,
                                                    +0x138 crc32, +0x13C compression

crc32 is of the uncompressed data. The layout is the one whose entry size divides the table and
whose entries all point inside the file. Read-only: building bundles is left to CDPR's tools
(REDkit / wcc_lite).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Optional

import lz4block

MAGIC = b"POTATO70"
HEADER_SIZE = 0x20
NAME_SIZE = 0x100
STORED, ZLIB, LZ4, LZ4_HC = 0, 1, 4, 5                  # compression types

# entry size -> positions of (offset, size, stored size, crc32, compression) within an entry
ENTRY_LAYOUTS = {
    0x130: (0x110, 0x118, 0x11C, 0x120, 0x124),
    0x140: (0x11C, 0x114, 0x118, 0x138, 0x13C),
}


@dataclass
class BundleEntry:
    name: str                     # path in the game's depot, e.g. gameplay\gui_new\...\x.redswf
    offset: int
    size: int                     # uncompressed
    stored_size: int              # as stored (compressed)
    crc: int
    compression: int


class Bundle:
    def __init__(self, data: bytes, lz4: lz4block.Decompressor = lz4block.DEFAULT) -> None:
        """data: the bundle's bytes (a memory map works too: only what's read is loaded).
        lz4: how LZ4 entries are decompressed (see lz4block)."""
        if data[:len(MAGIC)] != MAGIC:
            raise ValueError("not a REDengine 3 bundle")
        self.data, self.lz4 = data, lz4
        (table_size,) = struct.unpack_from("<I", data, 16)
        for entry_size, fields in ENTRY_LAYOUTS.items():
            if table_size % entry_size == 0:
                entries = [self._read_entry(HEADER_SIZE + entry_size * i, fields)
                           for i in range(table_size // entry_size)]
                if self._plausible(entries, HEADER_SIZE + table_size):
                    self.entries, self.entry_size = entries, entry_size
                    return
        raise ValueError(f"unrecognized bundle table layout (table size {table_size})")

    def _read_entry(self, start: int, fields: tuple[int, ...]) -> BundleEntry:
        name = bytes(self.data[start:start + NAME_SIZE]).split(b"\0")[0].decode("utf-8", "replace")
        offset, size, stored_size, crc, compression = (struct.unpack_from("<I", self.data, start + f)[0]
                                                        for f in fields)
        return BundleEntry(name, offset, size, stored_size, crc, compression)

    def _plausible(self, entries: list[BundleEntry], data_start: int) -> bool:
        """Whether a layout guess reads sensible entries: inside the file, known compression, and
        the first file right after the table (REDkit packs tightly; others align to 4 KB)."""
        return bool(entries) \
            and all(e.offset >= data_start and e.offset + e.stored_size <= len(self.data)
                    and e.compression in (STORED, ZLIB, LZ4, LZ4_HC) for e in entries) \
            and min(e.offset for e in entries) < data_start + 0x1000

    def find(self, path: str) -> Optional[BundleEntry]:
        """The entry for a depot path (either slash, any case), or None."""
        wanted = path.replace("/", "\\").lower()
        return next((e for e in self.entries if e.name.lower() == wanted), None)

    def read(self, entry: BundleEntry) -> bytes:
        """An entry's file, decompressed and CRC-checked."""
        stored = bytes(self.data[entry.offset:entry.offset + entry.stored_size])
        if entry.compression == STORED:
            out = stored
        elif entry.compression == ZLIB:
            out = zlib.decompress(stored)
        elif entry.compression in (LZ4, LZ4_HC):
            out = self.lz4(stored, entry.size)
        else:
            raise ValueError(f"{entry.name}: unsupported compression type {entry.compression}")
        if zlib.crc32(out) != entry.crc:
            raise ValueError(f"{entry.name}: CRC mismatch after decompression")
        return out
