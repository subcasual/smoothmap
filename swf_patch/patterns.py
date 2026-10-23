"""Patterns (patch-format.md "Patterns"): finding instruction sequences in a method.

A pattern is a list of elements, one per line: an instruction (`op operand`, `*` for any op,
`a|b` for alternatives) or the zero-width `<jump-target>` (the next instruction is one a jump
lands on). Debug instructions are skipped; labels only matter to `<jump-target>`.

Operands: a name, number or "string" must equal the instruction's; `*` matches anything;
`$name` captures the operand (`$name:inner` captures it only if `inner` matches);
`<runtime-name>` matches names taken from the stack at run time. Names are compared by their name
part only, whichever way they're written (`x`, `untyped x`, `Class.x`, `package::x`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple, Optional, Union

from .abcfile import ABC, MULTINAME_L, MULTINAME_LA, RTQNAME, RTQNAME_A, RTQNAME_L, RTQNAME_LA
from .assembler import code_lines, unquote
from .errors import PatchError
from .instructions import CALL_OPS, NAME_OPS, REGISTER_OPS, Code, Instruction, Label

JUMP_TARGET = "<jump-target>"
RUNTIME_NAME = "<runtime-name>"
RUNTIME_NAME_KINDS = {RTQNAME, RTQNAME_A, RTQNAME_L, RTQNAME_LA, MULTINAME_L, MULTINAME_LA}
PART_ORDER = ("after", "match", "before")      # a step's pattern parts, matched as one sequence


@dataclass
class Element:
    """One instruction of a pattern."""
    ops: Optional[set[str]]                     # None: any instruction
    operand: Optional[str]                      # None: any operand


PatternElement = Union[Element, str]            # an Element, or JUMP_TARGET
Pattern = list[PatternElement]


def parse(text: str, where: str = "") -> Pattern:
    elements: Pattern = []
    for line in code_lines(text):
        if line == JUMP_TARGET:
            elements.append(JUMP_TARGET)
            continue
        op, _, rest = line.partition(" ")
        rest = rest.strip()
        ops = None if op == "*" else set(op.split("|"))
        operand = rest.split(",")[0].strip() if rest else None
        elements.append(Element(ops, operand))
    if not any(isinstance(e, Element) for e in elements):
        raise PatchError(f"{where}: pattern has no instructions")
    return elements


class CodeEntry(NamedTuple):
    index: int                                  # position in MethodCode.items
    instruction: Instruction
    labelled: bool                              # a label comes before it (debug instructions aside)


class MethodCode:
    """A method's decoded items, and the view patterns match against: its instructions without
    the debug ones."""

    def __init__(self, items: Code) -> None:
        self.items = items
        self.entries: list[CodeEntry] = []
        labelled = False
        for index, item in enumerate(items):
            if isinstance(item, Label):
                labelled = True
            elif not item.op.startswith("debug"):
                self.entries.append(CodeEntry(index, item, labelled))
                labelled = False


@dataclass
class Match:
    parts: dict[str, list[int]]                 # part name -> item indices of its instructions
    captures: dict[str, Optional[int]] = field(default_factory=dict)
    jump_target_label: Optional[int] = None     # when the pattern starts with <jump-target>: the label's item index


def find(abc: ABC, code: MethodCode, parts: dict[str, Pattern]) -> list[Match]:
    """Every place where the parts (after, match, before; any of them), in that order, match
    consecutive instructions."""
    sequence = [(part, element) for part in PART_ORDER if part in parts for element in parts[part]]
    width = sum(1 for _, element in sequence if isinstance(element, Element))
    matches = (_match_at(abc, code, sequence, start) for start in range(len(code.entries) - width + 1))
    return [m for m in matches if m]


def _match_at(abc: ABC, code: MethodCode, sequence: list[tuple[str, PatternElement]], start: int) -> Optional[Match]:
    match = Match({part: [] for part, _ in sequence})
    position = start
    for i, (part, element) in enumerate(sequence):
        if element == JUMP_TARGET:
            if position >= len(code.entries) or not code.entries[position].labelled:
                return None
            if i == 0:          # code is inserted at the label, so it must be right before the instruction
                label_index = code.entries[start].index - 1
                if not isinstance(code.items[label_index], Label):
                    return None
                match.jump_target_label = label_index
            continue
        entry = code.entries[position]
        if element.ops is not None and entry.instruction.op not in element.ops:
            return None
        if not _operand_matches(abc, entry.instruction, element.operand, match.captures):
            return None
        match.parts[part].append(entry.index)
        position += 1
    return match


def _operand_matches(abc: ABC, instruction: Instruction, token: Optional[str], captures: dict) -> bool:
    if token is None or token == "*":
        return True
    arg = instruction.args[0] if instruction.args else None
    if token.startswith("$"):
        name, _, inner = token[1:].partition(":")
        if inner and not _operand_matches(abc, instruction, inner, captures):
            return False
        captures[name] = arg
        return True
    op = instruction.op
    if token == RUNTIME_NAME:
        return isinstance(arg, int) and abc.multinames[arg][0] in RUNTIME_NAME_KINDS
    if op in NAME_OPS or op in CALL_OPS:
        return abc.name_of(arg) == _name_part(token)
    if op == "pushstring":
        return token.startswith('"') and abc.string(arg) == unquote(token)
    try:
        if op == "pushint":
            return abc.ints[arg] == int(token)
        if op == "pushuint":
            return abc.uints[arg] == int(token)
        if op == "pushdouble":
            return abc.doubles[arg] == float(token)
        if op in REGISTER_OPS or op in ("pushbyte", "pushshort") or isinstance(arg, int):
            return arg == int(token)
    except ValueError:                          # the token isn't a number
        return False
    return False


def _name_part(token: str) -> str:
    """The name in a name operand, however it's qualified: untyped x, Class.x, package::x, "uri"::x."""
    if token.startswith("untyped "):
        return token[len("untyped "):].strip()
    return token.rpartition("::")[2].rpartition(".")[2]


def select(matches: list[Match], count: Union[int, str, None], where: str, description: str) -> list[Match]:
    """The matches, if there are as many as expected: `count` is a number (default 1) or "all"
    (at least one)."""
    if count == "all":
        if not matches:
            raise PatchError(f"{where}: {description} not found")
        return matches
    expected = 1 if count is None else int(count)
    if len(matches) != expected:
        raise PatchError(f"{where}: {description} expected {expected} match{'es' if expected != 1 else ''}, "
                         f"found {len(matches)}")
    return matches
