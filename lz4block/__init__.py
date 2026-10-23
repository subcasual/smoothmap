"""lz4block: LZ4 block decompression (https://github.com/lz4/lz4/blob/dev/doc/lz4_Block_format.md),
the raw block format (no frame header), as stored in REDengine 3 bundles. Decompression only.

Two interchangeable decompressors, each `(compressed bytes, uncompressed size) -> bytes`, raising
ValueError on bad data or a size mismatch:

    "python-lz4"  the lz4 package from PyPI (pip install lz4): C, about 100x faster
    "builtin"     pure Python, below: no dependency

DEFAULT is python-lz4 when it's installed, else builtin.
"""
from __future__ import annotations

from typing import Callable

try:
    import lz4.block as _lz4_block
except ImportError:
    _lz4_block = None

Decompressor = Callable[[bytes, int], bytes]

MIN_MATCH = 4


def decompress_builtin(src: bytes, out_size: int) -> bytes:
    """A block is a series of sequences. Each starts with a token byte: the high 4 bits are the number
    of literal bytes that follow, the low 4 bits the length of a match (minus 4) that copies earlier
    output. A field of 15 continues in extra bytes, each added to it, until a byte below 255. The last
    sequence has literals only."""
    out = bytearray()
    pos = 0

    def length(start: int) -> int:
        nonlocal pos
        if start != 15:
            return start
        while True:
            extra = src[pos]
            pos += 1
            start += extra
            if extra != 255:
                return start

    while pos < len(src):
        token = src[pos]
        pos += 1
        literals = length(token >> 4)
        out += src[pos:pos + literals]
        pos += literals
        if pos >= len(src):                              # the last sequence: literals only
            break
        offset = src[pos] | (src[pos + 1] << 8)          # how far back the match starts
        pos += 2
        match_length = length(token & 15) + MIN_MATCH
        start = len(out) - offset
        for i in range(match_length):                    # byte by byte: a match may overlap its own output
            out.append(out[start + i])
    return _checked(bytes(out), out_size)


def decompress_python_lz4(src: bytes, out_size: int) -> bytes:
    if _lz4_block is None:
        raise ImportError("the lz4 package isn't installed (pip install lz4)")
    try:
        out = _lz4_block.decompress(src, uncompressed_size=out_size)
    except _lz4_block.LZ4BlockError as e:
        raise ValueError(f"LZ4 block: {e}") from None
    return _checked(out, out_size)


def _checked(out: bytes, out_size: int) -> bytes:
    if len(out) != out_size:
        raise ValueError(f"LZ4 block decompressed to {len(out)} bytes, expected {out_size}")
    return out


DECOMPRESSORS: dict[str, Decompressor] = {"python-lz4": decompress_python_lz4, "builtin": decompress_builtin}
DEFAULT: Decompressor = decompress_python_lz4 if _lz4_block is not None else decompress_builtin


def decompressor(name: str) -> Decompressor:
    """A decompressor by name. Raises ValueError for an unknown name, or python-lz4 when the package
    isn't installed."""
    if name not in DECOMPRESSORS:
        raise ValueError(f"unknown LZ4 decompressor '{name}' (choose from {', '.join(DECOMPRESSORS)})")
    if name == "python-lz4" and _lz4_block is None:
        raise ValueError("python-lz4 isn't installed: pip install lz4, or choose builtin")
    return DECOMPRESSORS[name]
