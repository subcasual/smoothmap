"""Assembly text -> instructions, per patch-format.md "Code".

One instruction per line, `name:` labels, `#` comments. Name operands are resolved against the
ABC by `Names`; register instructions take declared local names or numbers; `push N` picks the
smallest push instruction for the number. `lookupswitch default, [case0, case1, ...]` takes labels.
"""
from __future__ import annotations

import re
from typing import Iterator

from .abcfile import ABC
from .errors import PatchError
from .instructions import (CALL_OPS, COUNT_OPS, INDEX_OPS, JUMP_OPS, NAME_OPS, OPERANDS_BY_NAME, REGISTER_OPS,
                           Code, Instruction, Label)
from .names import Names

LABEL_LINE = re.compile(r"^([A-Za-z_]\w*):$")             # a label definition: `name:`
SWITCH_OPERANDS = re.compile(r"^(\w+)\s*,\s*\[([\w\s,]*)\]$")    # lookupswitch default, [cases]


def code_lines(text: object) -> Iterator[str]:
    """The non-empty lines of assembly text (or a pattern), comments and indentation removed."""
    for line in str(text).splitlines():
        line = strip_comment(line).strip()
        if line:
            yield line


def strip_comment(line: str) -> str:
    """The line up to its `#` comment, if any; a `#` inside a "quoted string" doesn't count."""
    quoted = escaped = False
    for i, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == "#" and not quoted:
            return line[:i]
    return line


def parse_number(token: object) -> int | float:
    text = str(token).strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        raise PatchError(f"'{token}' is not a number")


def push_value(abc: ABC, value: int | float) -> Instruction:
    """The smallest instruction pushing `value`: pushbyte, pushshort, or pushdouble."""
    if isinstance(value, bool):
        raise PatchError("push: booleans use pushtrue/pushfalse")
    if isinstance(value, int) and -128 <= value <= 127:
        return Instruction("pushbyte", value)
    if isinstance(value, int) and -32768 <= value <= 32767:
        return Instruction("pushshort", value)
    return Instruction("pushdouble", abc.add_double(float(value)))


ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}


def quote(text: str) -> str:
    """A string as a "quoted" operand: backslash, quote, newline, return and tab escaped."""
    escaped = {value: "\\" + key for key, value in ESCAPES.items()}
    return '"' + "".join(escaped.get(char, char) for char in text) + '"'


def unquote(text: str) -> str:
    """The content of a "quoted string" operand: \\n, \\r, \\t, \\" and \\\\ are escapes."""
    out, chars = [], iter(text[1:-1])
    for char in chars:
        if char == "\\":
            escape = next(chars, "")
            if escape not in ESCAPES:
                raise PatchError(f"unknown escape \\{escape} in {text}")
            char = ESCAPES[escape]
        out.append(char)
    return "".join(out)


class Assembler:
    """Assembles the code of one method edit (or one new method). Labels are shared by everything
    assembled through the same instance, so one step can jump to a label another step defines."""

    def __init__(self, abc: ABC, names: Names, locals_map: dict[str, int] | None = None,
                 captures: dict[str, int] | None = None, where: str = "", context: int | None = None) -> None:
        """context: the class whose code this is (for `untyped name` and `<runtime-name>` operands)."""
        self.abc, self.names, self.where, self.context = abc, names, where, context
        self.locals = dict(locals_map or {})                       # local name -> register
        self.captures = captures if captures is not None else {}   # capture name -> operand
        self.labels: dict[str, Label] = {}                          # jumped to or defined
        self.defined: set[str] = set()

    def assemble(self, text: str) -> Code:
        code: Code = []
        for line in code_lines(text):
            label = LABEL_LINE.match(line)
            try:
                code.append(self._define_label(label.group(1)) if label else self._instruction(line))
            except PatchError as e:
                raise PatchError(f"{self.where}: '{line}': {e}")
        return code

    def check_labels(self) -> None:
        """Call once everything is assembled: every label jumped to must be defined."""
        missing = set(self.labels) - self.defined
        if missing:
            raise PatchError(f"{self.where}: jump to undefined label(s) {sorted(missing)}")

    def _define_label(self, name: str) -> Label:
        if name in self.defined:
            raise PatchError(f"label '{name}' defined twice")
        self.defined.add(name)
        return self._label(name)

    def _label(self, name: str) -> Label:
        return self.labels.setdefault(name, Label(name))

    def _instruction(self, line: str) -> Instruction:
        op, _, rest = line.partition(" ")
        rest = rest.strip()
        args = [a.strip() for a in rest.split(",")] if rest and op != "pushstring" else []
        if op == "push":
            return push_value(self.abc, parse_number(rest))
        if OPERANDS_BY_NAME.get(op) and not rest:
            raise PatchError(f"{op} needs an operand")
        if op in NAME_OPS:
            return Instruction(op, self._name(args[0]))
        if op in CALL_OPS:
            if len(args) != 2:
                raise PatchError(f"{op} takes a name and an argument count")
            return Instruction(op, self._name(args[0]), int(args[1]))
        if op in REGISTER_OPS:
            return Instruction(op, self._register(args[0]))
        if op == "hasnext2":
            if len(args) != 2:
                raise PatchError("hasnext2 takes two registers")
            return Instruction(op, self._register(args[0]), self._register(args[1]))
        if op == "pushbyte":
            value = int(args[0])
            if not -128 <= value <= 127:
                raise PatchError(f"pushbyte {value} doesn't fit a signed byte; use push")
            return Instruction(op, value)
        if op == "pushshort":
            value = int(args[0])
            if not -32768 <= value <= 32767:
                raise PatchError(f"pushshort {value} doesn't fit 16 bits; use push")
            return Instruction(op, value)
        if op == "pushint":
            value = int(args[0])
            if not -2**31 <= value < 2**31:
                raise PatchError(f"pushint {value} doesn't fit 32 bits")
            return Instruction(op, self.abc.add_int(value))
        if op == "pushuint":
            value = int(args[0])
            if not 0 <= value < 2**32:
                raise PatchError(f"pushuint {value} doesn't fit 32 bits unsigned")
            return Instruction(op, self.abc.add_uint(value))
        if op == "pushdouble":
            return Instruction(op, self.abc.add_double(float(args[0])))
        if op == "pushstring":
            if not (len(rest) >= 2 and rest[0] == rest[-1] == '"'):
                raise PatchError("pushstring needs a double-quoted string")
            return Instruction(op, self.abc.add_string(unquote(rest)))
        if op in COUNT_OPS or op in INDEX_OPS:
            return Instruction(op, int(args[0]))
        if op in JUMP_OPS:
            return Instruction(op, self._label(args[0]))
        if op == "lookupswitch":
            switch = SWITCH_OPERANDS.match(rest)
            if not switch:
                raise PatchError("lookupswitch takes a default label and a [list] of case labels")
            cases = [c.strip() for c in switch.group(2).split(",") if c.strip()]
            return Instruction(op, self._label(switch.group(1)), [self._label(c) for c in cases])
        if args:
            raise PatchError(f"unsupported instruction or operand: {op} {rest}")
        return Instruction(op)

    def _name(self, token: str) -> int:
        """A name operand: a $capture, or a name resolved by Names."""
        if token.startswith("$"):
            if token[1:] not in self.captures:
                raise PatchError(f"capture '{token}' is not defined")
            return self.captures[token[1:]]
        return self.names.operand(token, self.context)

    def _register(self, token: str) -> int:
        """A register operand: a number, a $capture, or a declared local's name."""
        if token.isdecimal():
            return int(token)
        if token.startswith("$") and token[1:] in self.captures:
            return self.captures[token[1:]]
        if token not in self.locals:
            raise PatchError(f"'{token}' is not a declared local")
        return self.locals[token]
