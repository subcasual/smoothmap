"""The SWF and GFX payloads around the ActionScript bytecode.

    SWF ("FWS", or "CWS": zlib-compressed) or GFX (Scaleform's variant: "GFX", or "CFX")
      └─ 8-byte header (signature, version, file length), then the body: frame size, frame rate and
         count, then tags
           └─ DoABC tag (82): flags, name, then the ABC bytecode

LZMA-compressed SWFs ("ZWS") aren't supported.
"""
from __future__ import annotations

import struct
import zlib
from typing import NamedTuple

DO_ABC_TAG = 82
LONG_TAG = 0x3F                   # a tag length of 63 means a 32-bit length follows


class _DoAbcTag(NamedTuple):
    """Where the DoABC tag is in a SWF body (offsets relative to the body)."""
    tag_start: int
    data_start: int               # the tag's data: flags (4 bytes), name (zero-terminated), ABC
    data_length: int
    abc_start: int


def _split(blob: bytes) -> tuple[bytes, bytes]:
    """(8-byte header, body). "CWS"/"CFX" bodies are zlib-compressed; "FWS"/"GFX" ones are not."""
    body = zlib.decompress(blob[8:]) if blob[:1] == b"C" else blob[8:]
    return blob[:8], body


def _find_do_abc(body: bytes) -> _DoAbcTag:
    data = b"\0" * 8 + body                          # positions below count from the file start
    rect_bits = 5 + 4 * (data[8] >> 3)               # the frame size RECT: 5 bits + 4 fields
    pos = 8 + (rect_bits + 7) // 8 + 4               # skip it, the frame rate and the frame count
    while pos < len(data):
        tag_start = pos
        (code,) = struct.unpack_from("<H", data, pos)
        pos += 2
        tag, length = code >> 6, code & LONG_TAG
        if length == LONG_TAG:
            (length,) = struct.unpack_from("<I", data, pos)
            pos += 4
        if tag == DO_ABC_TAG:
            name_end = data.index(b"\0", pos + 4)
            return _DoAbcTag(tag_start - 8, pos - 8, length, name_end + 1 - 8)
        pos += length
    raise ValueError("no DoABC tag")


def read_abc(blob: bytes) -> bytes:
    """The ABC bytecode inside a SWF or GFX payload."""
    _, body = _split(blob)
    tag = _find_do_abc(body)
    return body[tag.abc_start:tag.data_start + tag.data_length]


def replace_abc(blob: bytes, new_abc: bytes) -> bytes:
    """The payload with its bytecode replaced, fixing the tag length and the header's file length.

    The header length is adjusted by the size difference rather than recomputed, because CDPR's
    GFX files store a value that differs from the real length; keeping the difference preserves
    whatever convention the file uses.
    """
    header, body = _split(blob)
    tag = _find_do_abc(body)
    data = body[tag.data_start:tag.abc_start] + new_abc
    new_tag = struct.pack("<HI", (DO_ABC_TAG << 6) | LONG_TAG, len(data)) + data
    new_body = body[:tag.tag_start] + new_tag + body[tag.data_start + tag.data_length:]
    header = bytearray(header)
    (old_length,) = struct.unpack_from("<I", header, 4)
    struct.pack_into("<I", header, 4, old_length + len(new_body) - len(body))
    return bytes(header) + (zlib.compress(new_body, 9) if header[:1] == b"C" else new_body)
