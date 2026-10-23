"""The primitive encodings of the ABC format (AVM2 overview, section 4.1).

Most integers are variable-length: 7 bits per byte, low bits first, with the high bit set on every
byte except the last. "u30" holds unsigned values, "s32" signed ones; both take at most 5 bytes.
Jump offsets are fixed-width signed 24-bit values ("s24").
"""
from __future__ import annotations

import struct

MAX_VARINT_BITS = 35        # 5 bytes of 7 bits


class Reader:
    """Reads primitives from bytes, advancing a position."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        (value,) = struct.unpack_from("<H", self.data, self.pos)
        self.pos += 2
        return value

    def s24(self) -> int:
        return int.from_bytes(self.raw(3), "little", signed=True)

    def d64(self) -> float:
        (value,) = struct.unpack_from("<d", self.data, self.pos)
        self.pos += 8
        return value

    def raw(self, length: int) -> bytes:
        value = bytes(self.data[self.pos:self.pos + length])
        self.pos += length
        return value

    def u30(self) -> int:
        value, _ = self._varint()
        return value

    def s32(self) -> int:
        value, bits = self._varint()
        if bits < MAX_VARINT_BITS and value & (1 << (bits - 1)):
            value -= 1 << bits                          # sign-extend from the last bit read
        value &= 0xFFFFFFFF                             # 5-byte values: 32-bit two's complement
        return value - (1 << 32) if value & 0x80000000 else value

    def _varint(self) -> tuple[int, int]:
        """(value, number of bits read)."""
        value = bits = 0
        while True:
            byte = self.u8()
            value |= (byte & 0x7F) << bits
            bits += 7
            if not byte & 0x80 or bits >= MAX_VARINT_BITS:
                return value, bits


class Writer:
    """Appends primitives to a growing byte buffer."""

    def __init__(self) -> None:
        self.data = bytearray()

    def u8(self, value: int) -> None:
        self.data.append(value & 0xFF)

    def u16(self, value: int) -> None:
        self.data += struct.pack("<H", value)

    def s24(self, value: int) -> None:
        self.data += value.to_bytes(3, "little", signed=True)

    def d64(self, value: float) -> None:
        self.data += struct.pack("<d", value)

    def raw(self, value: bytes) -> None:
        self.data += value

    def u30(self, value: int) -> None:
        value &= 0xFFFFFFFF
        while True:
            byte, value = value & 0x7F, value >> 7
            if not value:
                self.data.append(byte)
                return
            self.data.append(byte | 0x80)

    def s32(self, value: int) -> None:
        while True:
            byte, value = value & 0x7F, value >> 7
            sign_bit = byte & 0x40
            if (value == 0 and not sign_bit) or (value == -1 and sign_bit):
                self.data.append(byte)
                return
            self.data.append(byte | 0x80)
