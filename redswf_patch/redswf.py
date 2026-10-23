"""CDPR's .redswf: a REDengine CR2W resource holding the Flash payloads.

    .redswf (REDengine CR2W resource)
      └─ CSwfResource chunk, ending in two length-prefixed buffers:
           1. GFX payload  (Scaleform's "CFX"/"GFX": what the game runs)
           2. source SWF   ("CWS"/"FWS": what REDkit re-imports from); empty in cooked files,
              i.e. those inside published bundles
             └─ DoABC tag  -> the ABC bytecode (the same in both payloads; see swf_patch.swf)

Nothing in here is specific to one game file.
"""
from __future__ import annotations

import struct
import zlib

from swf_patch.abcfile import ABC
from swf_patch.swf import read_abc

CR2W_HEADER_SIZE = 160
CR2W_TABLE_COUNT = 10
CHUNK_TABLE = 4                   # the header's table 4 lists the chunks; chunk 0 is CSwfResource
CHUNK_ENTRY = "<HHIIIII"          # chunk table entry: ..., [3] size, [4] offset, ..., [6] CRC32
CHUNK_SIZE, CHUNK_OFFSET, CHUNK_CRC = 3, 4, 6


class RedSwf:
    """A .redswf, read so it can be rebuilt with new GFX/SWF payloads.

    Everything REDengine checks is recomputed on rebuild: chunk sizes and offsets, per-chunk CRC32s,
    the chunk table's CRC, the file size fields, and the header CRC (CRC32 of the header with its
    CRC field set to 0xDEADBEEF). Rebuilding with the original payloads reproduces the input.
    """

    def __init__(self, data: bytes) -> None:
        self.data = bytearray(data)
        if self.data[:4] != b"CR2W":
            raise ValueError("not a CR2W file")
        self.tables = [list(struct.unpack_from("<III", self.data, 40 + 12 * i)) for i in range(CR2W_TABLE_COUNT)]
        table_offset, chunk_count, _ = self.tables[CHUNK_TABLE]
        self.chunk_table = table_offset
        entry_size = struct.calcsize(CHUNK_ENTRY)
        self.chunks = [list(struct.unpack_from(CHUNK_ENTRY, self.data, table_offset + entry_size * i))
                       for i in range(chunk_count)]
        self.swf_chunk_start = self.chunks[0][CHUNK_OFFSET]
        self.swf_chunk_end = self.swf_chunk_start + self.chunks[0][CHUNK_SIZE]

        # The chunk starts with its properties (name, type, size, value; ...), ended by a 0 name.
        pos = self.swf_chunk_start + 1
        while struct.unpack_from("<H", self.data, pos)[0] != 0:
            pos += 4 + struct.unpack_from("<I", self.data, pos + 4)[0]
        self.buffers_start = pos + 2

        buffers = []
        pos = self.buffers_start
        while pos < self.swf_chunk_end:
            (length,) = struct.unpack_from("<I", self.data, pos)
            buffers.append(bytes(self.data[pos + 4:pos + 4 + length]))
            pos += 4 + length
        if pos != self.swf_chunk_end or len(buffers) != 2 or buffers[0][1:3] != b"FX":
            raise ValueError("unexpected CSwfResource layout")
        self.gfx = buffers[0]
        self.cooked = len(buffers[1]) == 0                # cooked files keep the slot but leave it empty
        self.swf = None if self.cooked else buffers[1]

    def build(self, gfx: bytes, swf: bytes | None = None) -> bytes:
        """The file with new payloads (swf only for depot files, i.e. not cooked)."""
        if (swf is None) != self.cooked:
            raise ValueError("payload count must match the original file")
        swf = swf if swf is not None else b""
        buffers = struct.pack("<I", len(gfx)) + gfx + struct.pack("<I", len(swf)) + swf
        out = bytearray(self.data[:self.buffers_start]) + buffers + self.data[self.swf_chunk_end:]
        growth = len(out) - len(self.data)

        entry_size = struct.calcsize(CHUNK_ENTRY)
        for i, chunk in enumerate(self.chunks):
            chunk = list(chunk)
            if i == 0:
                chunk[CHUNK_SIZE] += growth
            elif chunk[CHUNK_OFFSET] >= self.swf_chunk_end:
                chunk[CHUNK_OFFSET] += growth
            start = chunk[CHUNK_OFFSET]
            chunk[CHUNK_CRC] = zlib.crc32(bytes(out[start:start + chunk[CHUNK_SIZE]]))
            struct.pack_into(CHUNK_ENTRY, out, self.chunk_table + entry_size * i, *chunk)

        table = self.tables[CHUNK_TABLE]
        table[2] = zlib.crc32(bytes(out[table[0]:table[0] + entry_size * table[1]]))
        struct.pack_into("<III", out, 40 + 12 * CHUNK_TABLE, *table)

        struct.pack_into("<I", out, 24, len(out))            # file size
        struct.pack_into("<I", out, 28, len(out))            # buffer size
        struct.pack_into("<I", out, 32, 0xDEADBEEF)
        struct.pack_into("<I", out, 32, zlib.crc32(bytes(out[:CR2W_HEADER_SIZE])))
        return bytes(out)


def load_abc(path: str, source_swf: bool = False) -> ABC:
    """The bytecode of a .redswf: its GFX payload (what the game runs), or its source SWF. A plain
    SWF or GFX file works too."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"CR2W":
        if source_swf:
            raise ValueError(f"{path} is not a .redswf: it has no separate source SWF")
        return ABC(read_abc(data))
    resource = RedSwf(data)
    if source_swf and resource.swf is None:
        raise ValueError(f"{path} is cooked: it has no source SWF")
    return ABC(read_abc(resource.swf if source_swf else resource.gfx))
