"""AVM2 instructions: the opcode table, and decoding/encoding a method body's code.

Decoded code is a list of `Instruction`s and `Label`s. A label sits right before the instruction a
jump lands on, and jump operands are the `Label` objects themselves, so code can be edited (items
inserted or removed) without recomputing byte offsets; `encode` lays the offsets out again.
"""
from __future__ import annotations

import itertools
from typing import Iterator, Union

from .bytestream import Reader, Writer

# Opcode, name, operand encodings:
#   u30     variable-length unsigned integer (pool indices, counts, registers)
#   u8      unsigned byte
#   byte    signed byte (pushbyte)
#   jump    s24 offset from the end of the instruction (decoded to a Label)
#   switch  lookupswitch: s24 default offset, u30 case count - 1, then s24 case offsets, all
#           relative to the start of the instruction (decoded to a Label and a list of Labels)
_OPCODE_TABLE = """
0x01 bkpt
0x02 nop
0x03 throw
0x04 getsuper        u30
0x05 setsuper        u30
0x06 dxns            u30
0x07 dxnslate
0x08 kill            u30
0x09 label
0x0c ifnlt           jump
0x0d ifnle           jump
0x0e ifngt           jump
0x0f ifnge           jump
0x10 jump            jump
0x11 iftrue          jump
0x12 iffalse         jump
0x13 ifeq            jump
0x14 ifne            jump
0x15 iflt            jump
0x16 ifle            jump
0x17 ifgt            jump
0x18 ifge            jump
0x19 ifstricteq      jump
0x1a ifstrictne      jump
0x1b lookupswitch    switch
0x1c pushwith
0x1d popscope
0x1e nextname
0x1f hasnext
0x20 pushnull
0x21 pushundefined
0x23 nextvalue
0x24 pushbyte        byte
0x25 pushshort       u30
0x26 pushtrue
0x27 pushfalse
0x28 pushnan
0x29 pop
0x2a dup
0x2b swap
0x2c pushstring      u30
0x2d pushint         u30
0x2e pushuint        u30
0x2f pushdouble      u30
0x30 pushscope
0x31 pushnamespace   u30
0x32 hasnext2        u30 u30
0x35 li8
0x36 li16
0x37 li32
0x38 lf32
0x39 lf64
0x3a si8
0x3b si16
0x3c si32
0x3d sf32
0x3e sf64
0x40 newfunction     u30
0x41 call            u30
0x42 construct       u30
0x43 callmethod      u30 u30
0x44 callstatic      u30 u30
0x45 callsuper       u30 u30
0x46 callproperty    u30 u30
0x47 returnvoid
0x48 returnvalue
0x49 constructsuper  u30
0x4a constructprop   u30 u30
0x4c callproplex     u30 u30
0x4e callsupervoid   u30 u30
0x4f callpropvoid    u30 u30
0x50 sxi1
0x51 sxi8
0x52 sxi16
0x53 applytype       u30
0x55 newobject       u30
0x56 newarray        u30
0x57 newactivation
0x58 newclass        u30
0x59 getdescendants  u30
0x5a newcatch        u30
0x5d findpropstrict  u30
0x5e findproperty    u30
0x5f finddef         u30
0x60 getlex          u30
0x61 setproperty     u30
0x62 getlocal        u30
0x63 setlocal        u30
0x64 getglobalscope
0x65 getscopeobject  u8
0x66 getproperty     u30
0x68 initproperty    u30
0x6a deleteproperty  u30
0x6c getslot         u30
0x6d setslot         u30
0x6e getglobalslot   u30
0x6f setglobalslot   u30
0x70 convert_s
0x71 esc_xelem
0x72 esc_xattr
0x73 convert_i
0x74 convert_u
0x75 convert_d
0x76 convert_b
0x77 convert_o
0x78 checkfilter
0x80 coerce          u30
0x81 coerce_b
0x82 coerce_a
0x83 coerce_i
0x84 coerce_d
0x85 coerce_s
0x86 astype          u30
0x87 astypelate
0x88 coerce_u
0x89 coerce_o
0x90 negate
0x91 increment
0x92 inclocal        u30
0x93 decrement
0x94 declocal        u30
0x95 typeof
0x96 not
0x97 bitnot
0xa0 add
0xa1 subtract
0xa2 multiply
0xa3 divide
0xa4 modulo
0xa5 lshift
0xa6 rshift
0xa7 urshift
0xa8 bitand
0xa9 bitor
0xaa bitxor
0xab equals
0xac strictequals
0xad lessthan
0xae lessequals
0xaf greaterthan
0xb0 greaterequals
0xb1 instanceof
0xb2 istype          u30
0xb3 istypelate
0xb4 in
0xc0 increment_i
0xc1 decrement_i
0xc2 inclocal_i      u30
0xc3 declocal_i      u30
0xc4 negate_i
0xc5 add_i
0xc6 subtract_i
0xc7 multiply_i
0xd0 getlocal0
0xd1 getlocal1
0xd2 getlocal2
0xd3 getlocal3
0xd4 setlocal0
0xd5 setlocal1
0xd6 setlocal2
0xd7 setlocal3
0xef debug           u8 u30 u8 u30
0xf0 debugline       u30
0xf1 debugfile       u30
0xf2 bkptline        u30
0xf3 timestamp
"""


def _parse_opcode_table(table: str) -> dict[int, tuple[str, tuple[str, ...]]]:
    opcodes = {}
    for line in table.strip().splitlines():
        code, name, *operands = line.split()
        opcodes[int(code, 16)] = (name, tuple(operands))
    return opcodes


OPCODES = _parse_opcode_table(_OPCODE_TABLE)            # opcode -> (name, operand encodings)
OPCODE_BY_NAME = {name: code for code, (name, _) in OPCODES.items()}
OPERANDS_BY_NAME = {name: operands for name, operands in OPCODES.values()}

# Instruction groups, by what their operands mean (used by the assembler, patterns and tools).
JUMP_OPS = {name for name, operands in OPCODES.values() if operands == ("jump",)}
NAME_OPS = {"getproperty", "setproperty", "initproperty", "getlex", "findpropstrict", "findproperty",
            "coerce", "getsuper", "setsuper", "astype", "istype", "deleteproperty", "getdescendants"}
CALL_OPS = {"callproperty", "callpropvoid", "constructprop", "callproplex", "callsuper", "callsupervoid"}
REGISTER_OPS = {"getlocal", "setlocal", "kill", "inclocal", "declocal", "inclocal_i", "declocal_i"}
COUNT_OPS = {"constructsuper", "construct", "call", "newobject", "newarray", "applytype"}
INDEX_OPS = {"getscopeobject", "getslot", "setslot", "getglobalslot", "setglobalslot"}   # a plain index
NUMBER_PUSHES = {"pushbyte", "pushshort", "pushint", "pushuint", "pushdouble"}
# getlocal0..3 and setlocal0..3 carry their register in the opcode: name -> (getlocal|setlocal, register)
SHORT_REGISTER_OPS = {f"{op}{n}": (op, n) for op in ("getlocal", "setlocal") for n in range(4)}


class Label:
    """A jump target. Decoded labels are numbered L1, L2, ... in the order they're created."""

    _numbers = itertools.count(1)

    def __init__(self, name: str | None = None) -> None:
        self.name = name or f"L{next(Label._numbers)}"
        # A jump whose target isn't an instruction boundary (only in dead code) keeps its original
        # s24 offset here, so it can be written back unchanged.
        self.raw_offset: int | None = None

    def __repr__(self) -> str:
        return self.name


class Instruction:
    """An instruction: its name and operands. Jump operands are Labels; lookupswitch's are a
    default Label and a list of case Labels."""

    __slots__ = ("op", "args")

    def __init__(self, op: str, *args: Operand) -> None:
        self.op = op
        self.args = list(args)

    def __repr__(self) -> str:
        return f"{self.op} {', '.join(map(str, self.args))}".strip()


Operand = Union[int, Label, list]
Code = list[Union[Instruction, Label]]


def labels_in(args: list[Operand]) -> Iterator[Label]:
    """The jump targets among an instruction's operands (lookupswitch has a list of them)."""
    for arg in args:
        for target in arg if isinstance(arg, list) else [arg]:
            if isinstance(target, Label):
                yield target


# --------------------------------------------------------------------------- decoding

def decode(code: bytes) -> Code:
    """Method body bytes -> instructions, with a Label before every instruction a jump lands on."""
    reader = Reader(code)
    decoded: list[tuple[int, Instruction]] = []          # (byte offset, instruction)
    labels: dict[int, Label] = {}                         # target byte offset -> label

    def label_at(offset: int) -> Label:
        return labels.setdefault(offset, Label())

    while reader.pos < len(code):
        start = reader.pos
        opcode = reader.u8()
        if opcode not in OPCODES:
            raise ValueError(f"unknown opcode {opcode:#x} at {start}")
        name, operands = OPCODES[opcode]
        args: list[Operand] = []
        for encoding in operands:
            if encoding == "u30":
                value = reader.u30()
                if name == "pushshort" and value >= 1 << 31:     # stored as 32 bits; the value is signed
                    value -= 1 << 32
                args.append(value)
            elif encoding == "u8":
                args.append(reader.u8())
            elif encoding == "byte":
                value = reader.u8()
                args.append(value - 256 if value > 127 else value)
            elif encoding == "jump":
                offset = reader.s24()
                target = label_at(reader.pos + offset)
                target.raw_offset = offset
                args.append(target)
            elif encoding == "switch":
                default = reader.s24()
                case_count = reader.u30() + 1
                cases = [reader.s24() for _ in range(case_count)]
                args.append(label_at(start + default))
                args.append([label_at(start + case) for case in cases])
        decoded.append((start, Instruction(name, *args)))

    items: Code = []
    for offset, instruction in decoded:
        if offset in labels:
            items.append(labels[offset])
        items.append(instruction)
    if len(code) in labels:                               # a jump to the very end
        items.append(labels[len(code)])
    return items


# --------------------------------------------------------------------------- encoding

def encode(items: Code) -> bytes:
    """Instructions and labels -> method body bytes. Jump offsets are fixed-width, so every
    instruction's size is known before its targets are: one pass lays out the labels, a second
    writes the bytes."""
    label_offsets: dict[Label, int] = {}
    size = 0
    for item in items:
        if isinstance(item, Label):
            label_offsets[item] = size
        else:
            size += len(_encode_instruction(item, size, None))

    out = bytearray()
    for item in items:
        if isinstance(item, Instruction):
            out += _encode_instruction(item, len(out), label_offsets)
    return bytes(out)


def _encode_instruction(instruction: Instruction, start: int, label_offsets: dict[Label, int] | None) -> bytes:
    """One instruction's bytes. Without label offsets (the layout pass), jump offsets are 0."""
    op, args = instruction.op, iter(instruction.args)
    writer = Writer()
    writer.u8(OPCODE_BY_NAME[op])
    for encoding in OPERANDS_BY_NAME[op]:
        arg = next(args)
        if encoding == "u30":
            writer.u30(arg)
        elif encoding == "u8":
            writer.u8(arg)
        elif encoding == "byte":
            if not -128 <= arg <= 255:                   # signed byte, or a raw 0..255 from decoding
                raise ValueError(f"{op} operand {arg} doesn't fit in a byte")
            if op == "pushbyte" and arg > 127:
                raise ValueError(f"pushbyte {arg} doesn't fit in a signed byte (-128..127); use pushshort")
            writer.u8(arg)
        elif encoding == "jump":
            end = start + len(writer.data) + 3
            if label_offsets is None:
                writer.s24(0)
            elif arg in label_offsets:
                writer.s24(label_offsets[arg] - end)
            else:                                        # dead jump to a non-instruction offset
                writer.s24(arg.raw_offset)
        elif encoding == "switch":
            cases = next(args)

            def offset_of(target: Label) -> int:
                # Targets that aren't instruction boundaries (dead code) are written as offset 0.
                return 0 if label_offsets is None else label_offsets.get(target, start) - start

            writer.s24(offset_of(arg))
            writer.u30(len(cases) - 1)
            for case in cases:
                writer.s24(offset_of(case))
    return bytes(writer.data)
