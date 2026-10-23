"""Locating edits within the original method, for patches that change only what was edited.

    FFDec(exported) --diff--> FFDec(edited)       the hunks: instructions inserted, replaced, deleted
    FFDec(exported) --align-> original (CDPR's)   where each hunk goes in the original code

The two FFDec compiles differ only where the source was edited. FFDec's code and CDPR's differ in
their compilers' habits, so they're aligned the way patterns compare code: names by name only,
`initproperty` and `setproperty` alike, numbers by value whatever push instruction holds them. A
hunk can be placed only where the instructions on both sides of it align and the original has
exactly the instructions it replaces; each step is then anchored on the original's own instructions
around it, with as much context as it takes to match exactly one place.

Not handled yet (the caller replaces the whole method instead): changes the original code jumps
into, changes replacing a labelled span, and changes next to a label on both sides.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from swf_patch import patterns
from swf_patch.abcfile import ABC
from swf_patch.assembler import quote
from swf_patch.instructions import (CALL_OPS, COUNT_OPS, INDEX_OPS, JUMP_OPS, NAME_OPS, NUMBER_PUSHES, REGISTER_OPS,
                                    SHORT_REGISTER_OPS, Instruction, Label, decode)
from swf_patch.listing import format_operands
from swf_patch.names import Names

from .patches import Step
from .versions import ClassVersions, Version

MAX_CONTEXT = 8                    # instructions of context on a side before giving up
LABEL = "<label>"


@dataclass
class Line:
    """One instruction or label of a method, as aligned."""
    key: str                       # what alignment compares
    item: int                      # its index in the method's items (debug instructions included)
    instruction: Instruction | None = None    # None: a label


@dataclass
class Placement:
    steps: list[Step]
    new_locals: dict[int, int] = field(default_factory=dict)   # FFDec's register -> new local number (0-based)
    expected: list[str] = field(default_factory=list)          # the patched method's code, for verification
    instructions: list[Instruction] = field(default_factory=list)   # the new instructions, all steps


@dataclass
class _Method:
    """One version of the method, as aligned."""
    abc: ABC
    names: Names
    index: int                     # the method's index
    class_index: int | None        # its class's (for names private to it)
    lines: list[Line]
    code: patterns.MethodCode

    @classmethod
    def of(cls, version: Version, member: str) -> _Method:
        index = version.methods[member]
        lines, code = method_lines(version.abc, index)
        return cls(version.abc, version.names, index, version.cls, lines, code)

    def label(self, line: Line) -> Label:
        return self.code.items[line.item]


@dataclass
class _Hunk:
    """A change: the original lines [start, end) become the edited compile's lines `added`."""
    start: int
    end: int
    added: list[Line]
    label_before: bool             # the exported compile has a label right before the change
    label_after: bool              # ... or right after it


# --------------------------------------------------------------------------- lines

def method_lines(abc: ABC, index: int) -> tuple[list[Line], patterns.MethodCode]:
    code = patterns.MethodCode(decode(abc.methods[index].body.code))
    lines = []
    for item_index, item in enumerate(code.items):
        if isinstance(item, Label):
            lines.append(Line(LABEL, item_index))
        elif not item.op.startswith("debug"):
            lines.append(Line(_key(abc, item), item_index, item))
    return lines, code


def _key(abc: ABC, instruction: Instruction) -> str:
    """An instruction as alignment compares it (the way patterns do: names by name only)."""
    op, args = instruction.op, instruction.args
    if op in SHORT_REGISTER_OPS:
        return "{} {}".format(*SHORT_REGISTER_OPS[op])
    if op == "initproperty":
        op = "setproperty"
    if op in NAME_OPS or op in CALL_OPS:
        name = abc.name_of(args[0]) or "<runtime-name>"
        return f"{op} {name}" + (f", {args[1]}" if op in CALL_OPS else "")
    if op in NUMBER_PUSHES:
        return f"push {_number(abc, instruction)}"
    if op == "pushstring":
        return f"pushstring {quote(abc.string(args[0]))}"
    if op in JUMP_OPS or op == "lookupswitch":
        return op
    return f"{op} {', '.join(str(a) for a in args)}".strip()


def _number(abc: ABC, instruction: Instruction) -> float:
    op, arg = instruction.op, instruction.args[0]
    value = {"pushint": lambda: abc.ints[arg], "pushuint": lambda: abc.uints[arg],
             "pushdouble": lambda: abc.doubles[arg]}.get(op, lambda: arg)()
    return float(value)


def pattern_line(abc: ABC, instruction: Instruction) -> str:
    """An original instruction as a pattern line matching it: names by name, numbers and strings by
    value, jumps without targets (a pattern can't name labels)."""
    op, args = instruction.op, instruction.args
    if not args or op in JUMP_OPS or op == "lookupswitch":
        operand = ""
    elif op in NAME_OPS or op in CALL_OPS:
        operand = abc.name_of(args[0]) or "*"
    elif op == "pushstring":
        operand = quote(abc.string(args[0]))
    elif op == "pushdouble":
        value = abc.doubles[args[0]]
        operand = repr(value) if value == value else "*"          # NaN never compares equal
    elif op in ("pushint", "pushuint"):
        operand = str(int(_number(abc, instruction)))
    elif op in REGISTER_OPS or op in COUNT_OPS or op in INDEX_OPS or op in ("pushbyte", "pushshort", "hasnext2"):
        operand = str(args[0])
    else:
        operand = "*"
    return f"{op:<16}{operand}".rstrip()


def registers_of(instruction: Instruction) -> list[int]:
    if instruction.op in SHORT_REGISTER_OPS:
        return [SHORT_REGISTER_OPS[instruction.op][1]]
    if instruction.op in REGISTER_OPS:
        return [instruction.args[0]]
    if instruction.op == "hasnext2":
        return list(instruction.args)
    return []


def split_line(line: str) -> tuple[str, str]:
    """(op, operands) of a disassembled line, getlocal0..3 / setlocal0..3 as getlocal N / setlocal N."""
    op, _, operands = line.strip().partition(" ")
    if op in SHORT_REGISTER_OPS:
        long_op, register = SHORT_REGISTER_OPS[op]
        return long_op, str(register)
    return op, operands.strip()


def long_form(line: str) -> str:
    """A disassembled line compared loosely: one space after the op, getlocal1 as getlocal 1."""
    return " ".join(filter(None, split_line(line)))


# --------------------------------------------------------------------------- placing

def place(versions: ClassVersions, member: str, new_names: dict[int, str]) -> Placement | None:
    """Steps changing the original method exactly as the edit changed FFDec's compile of it, or None
    if some change can't be placed (yet). new_names: names for the edited compile's registers (used
    for those that become new locals).

    New code may branch: jumps within it get labels of their own; a jump to a label of the original
    code gets a landing label, inserted by a step of its own at the original label's place (patch
    code can't name the original's labels). Changes to code the original jumps into aren't placed."""
    o, a, b = (_Method.of(version, member) for version in versions.all)
    if _dead_jumps(o) or _dead_jumps(b):
        return None
    found = _hunks(o, a, b, len(b.abc.methods[b.index].param_types))
    if found is None:
        return None
    hunks, new_locals, landings = found

    # Labels the new code defines or lands on, named in order of appearance (one namespace per edit).
    added_lines = [line for hunk in hunks for line in hunk.added]
    label_names: dict[int, str] = {}
    for x in b.lines:
        if x.instruction is None and (id(b.label(x)) in landings or any(x is y for y in added_lines)):
            label_names[id(b.label(x))] = f"target{len(label_names) + 1}"
    renamed = {register: new_names.get(register, f"local{register}") for register in new_locals}
    steps = []
    landing_at: dict[int, list[int]] = {}             # original instruction line -> landing labels before it
    for target, original_line in landings.items():
        following = next((k for k in range(original_line + 1, len(o.lines)) if o.lines[k].instruction), None)
        if following is None:
            return None
        step = _anchor(o, following, following, label_before=True, label_after=False)
        if step is None:
            return None
        step.code = [f"{label_names[target]}:"]
        steps.append(step)                            # listed first: at a shared place, the label comes first
        landing_at.setdefault(following, []).append(target)
    for hunk in hunks:
        step = _anchor(o, hunk.start, hunk.end, hunk.label_before, hunk.label_after)
        if step is None:
            return None
        step.code = [_new_line(b, x, renamed, label_names) for x in hunk.added]
        steps.append(step)
    first_new_local = o.abc.methods[o.index].body.local_count
    expected = _expected(o, b, hunks, landing_at, {r: str(first_new_local + n) for r, n in new_locals.items()})
    return Placement(steps, new_locals, expected, [x.instruction for x in added_lines if x.instruction])


def _hunks(o: _Method, a: _Method, b: _Method, parameters: int
           ) -> tuple[list[_Hunk], dict[int, int], dict[int, int]] | None:
    """The changes between the exported (a) and edited (b) compiles, placed in the original (o):
    (hunks, the edited compile's new registers -> new local number, id(label in b) -> the original
    line its jumps land on), or None if one can't be placed."""
    keys_a, keys_b, keys_o = ([x.key for x in m.lines] for m in (a, b, o))
    a_to_o, a_to_b = _equal_map(keys_a, keys_o), _equal_map(keys_a, keys_b)
    b_to_a = {j: i for i, j in a_to_b.items()}
    label_line_b = {id(b.label(x)): k for k, x in enumerate(b.lines) if x.instruction is None}
    new_locals: dict[int, int] = {}
    landings: dict[int, int] = {}
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        added = b.lines[j1:j2]
        if any(x.instruction is None for x in a.lines[i1:i2]) or i1 == 0 or i2 >= len(a.lines):
            return None
        if i1 - 1 not in a_to_o or i2 not in a_to_o:
            return None
        start, end = a_to_o[i1 - 1] + 1, a_to_o[i2]
        if end - start != i2 - i1 or any(a_to_o.get(i) != start + (i - i1) for i in range(i1, i2)):
            return None                                   # the original differs around the change
        inside = {id(b.label(x)) for x in added if x.instruction is None}
        if any(not j1 <= k < j2 and x.instruction and any(id(t) in inside for t in _targets(x.instruction))
               for k, x in enumerate(b.lines)):
            return None                                   # code outside the change jumps into it
        for x in added:
            for target in _targets(x.instruction) if x.instruction else []:
                if id(target) not in inside:
                    exported_line = b_to_a.get(label_line_b.get(id(target), -1))
                    original_line = a_to_o.get(exported_line) if exported_line is not None else None
                    if original_line is None or o.lines[original_line].instruction is not None:
                        return None
                    landings[id(target)] = original_line
        # Registers: parameters as they are; FFDec's new registers become new locals; registers the
        # exported code already used only where alignment shows all three compiles agree on them.
        for x in added:
            for register in registers_of(x.instruction) if x.instruction else []:
                if register <= parameters or register in new_locals:
                    continue
                used_in_a = any(register in registers_of(y.instruction) for y in a.lines if y.instruction)
                if not used_in_a:
                    new_locals[register] = len(new_locals)
                elif not (_agree(a.lines, o.lines, a_to_o, register) and _agree(a.lines, b.lines, a_to_b, register)):
                    return None
        hunks.append(_Hunk(start, end, added, a.lines[i1 - 1].instruction is None, a.lines[i2].instruction is None))
    return hunks, new_locals, landings


def _targets(instruction: Instruction) -> list[Label]:
    if instruction.op in JUMP_OPS:
        return [instruction.args[0]]
    if instruction.op == "lookupswitch":
        return [instruction.args[0], *instruction.args[1]]
    return []


def _dead_jumps(method: _Method) -> bool:
    """Whether some jump targets a label that isn't in the code (a dead jump into an instruction)."""
    placed = {id(item) for item in method.code.items if isinstance(item, Label)}
    return any(id(t) not in placed for x in method.lines if x.instruction for t in _targets(x.instruction))


def _new_line(b: _Method, line: Line, renamed: dict[int, str], label_names: dict[int, str]) -> str:
    """A line of new code as assembly: a label, a jump to named labels, or an instruction."""
    if line.instruction is None:
        return f"{label_names[id(b.label(line))]}:"
    targets = _targets(line.instruction)
    if line.instruction.op == "lookupswitch":
        default, *cases = (label_names[id(t)] for t in targets)
        return f"{'lookupswitch':<16}{default}, [{', '.join(cases)}]"
    if targets:
        return f"{line.instruction.op:<16}{label_names[id(targets[0])]}"
    return _code_line(b.abc, b.names, line.instruction, b.class_index, renamed)


def _equal_map(left: list[str], right: list[str]) -> dict[int, int]:
    mapping = {}
    for tag, i1, i2, j1, _ in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if tag == "equal":
            mapping.update({i1 + k: j1 + k for k in range(i2 - i1)})
    return mapping


def _agree(left: list[Line], right: list[Line], mapping: dict[int, int], register: int) -> bool:
    """Whether some aligned instruction pair uses the register in both (so it means the same)."""
    return any(left[i].instruction and register in registers_of(left[i].instruction)
               and right[j].instruction and register in registers_of(right[j].instruction)
               for i, j in mapping.items())


def _anchor(o: _Method, start: int, end: int, label_before: bool, label_after: bool) -> Step | None:
    """A step at the original's lines [start, end) (start == end: an insert), with the least
    context matching only there. A label right after the change means the new code must come before
    it (an `after:` anchor); a label right before, after it (a `before:` anchor); labels on both
    sides can't be anchored."""
    if label_before and label_after:
        return None
    previous = [x for x in o.lines[:start] if x.instruction][::-1]
    following = [x for x in o.lines[end:] if x.instruction]
    replaced = [x for x in o.lines[start:end] if x.instruction]
    for size in range(1, MAX_CONTEXT + 1):
        after = previous[:size][::-1] if not label_before else []
        before = following[:size] if not label_after else []
        if not after and not before and not replaced:
            return None
        parts = {name: [pattern_line(o.abc, x.instruction) for x in lines]
                 for name, lines in (("after", after), ("match", replaced), ("before", before)) if lines}
        matches = patterns.find(o.abc, o.code, {name: patterns.parse("\n".join(text)) for name, text in parts.items()})
        expected = {name: [x.item for x in lines] for name, lines in (("after", after), ("match", replaced),
                                                                       ("before", before)) if lines}
        if len(matches) == 1 and all(matches[0].parts[name] == items for name, items in expected.items()):
            return Step(parts.get("after", []), parts.get("match", []), parts.get("before", []), [])
        if size > len(previous) and size > len(following):
            return None
    return None


def _code_line(abc: ABC, names: Names, instruction: Instruction, class_index: int | None,
               new_names: dict[int, str]) -> str:
    """A new instruction as assembly; its new registers by their local names."""
    if instruction.op in SHORT_REGISTER_OPS and SHORT_REGISTER_OPS[instruction.op][1] in new_names:
        long_op, register = SHORT_REGISTER_OPS[instruction.op]
        return f"{long_op:<16}{new_names[register]}"
    if instruction.op in REGISTER_OPS or instruction.op == "hasnext2":
        operands = ", ".join(new_names.get(r, str(r)) for r in instruction.args)
        return f"{instruction.op:<16}{operands}"
    return f"{instruction.op:<16}{format_operands(abc, instruction, None, names, class_index)}".rstrip()


def _expected(o: _Method, b: _Method, hunks: list[_Hunk], landing_at: dict[int, list[int]],
              numbers: dict[int, str]) -> list[str]:
    """What the patched method's code must be, as the disassembler shows it: the original's code with
    each change made and each landing label inserted, its new registers numbered as the edit numbers
    them. Labels at the same place are one label once encoded, so adjacent ones are merged; labels
    are then numbered in order, like the disassembler numbers them."""
    class_o = o.names.class_of_method(o.index)
    # entries: ("label", key, item position) or ("op", text, [target keys]); keys tell labels apart
    entries: list[tuple] = []
    changes = {hunk.start: hunk for hunk in hunks}
    skip_until = 0
    for position in range(len(o.lines) + 1):
        line = o.lines[position] if position < len(o.lines) else None
        if line is not None and line.instruction is not None:
            for target in landing_at.get(position, []):
                entries.append(("label", ("edited", target), line.item - 0.5))
        if position in changes:
            hunk = changes[position]
            for x in hunk.added:
                if x.instruction is None:
                    entries.append(("label", ("edited", id(b.label(x))), None))
                else:
                    entries.append(_entry(b.abc, b.names, b.class_index, x.instruction, "edited", numbers))
            skip_until = hunk.end
        if line is None or position < skip_until:
            continue
        if line.instruction is None:
            entries.append(("label", ("original", id(o.label(line))), line.item))
        else:
            entries.append(_entry(o.abc, o.names, class_o, line.instruction, "original", {}))
    # Merge adjacent labels (unless a debug instruction of the original separates them), number them.
    canonical: dict[tuple, int] = {}
    previous = None
    for entry in entries:
        if entry[0] == "label":
            if previous is not None and previous[0] == "label" and not _debug_between(o.code, previous[2], entry[2]):
                canonical[entry[1]] = canonical[previous[1]]
            else:
                canonical[entry[1]] = len(set(canonical.values()))
        previous = entry
    numbered: dict[int, int] = {}                     # by where labels are, as the disassembler numbers them
    for entry in entries:
        if entry[0] == "label":
            numbered.setdefault(canonical[entry[1]], len(numbered))
    out = []
    for entry in entries:
        if entry[0] == "label":
            if not out or out[-1] != f"L{numbered[canonical[entry[1]]]}:":
                out.append(f"L{numbered[canonical[entry[1]]]}:")
            continue
        _, text, targets = entry
        names = [f"L{numbered[canonical[key]]}" for key in targets]
        if text == "lookupswitch":
            out.append(f"lookupswitch {names[0]}, [{', '.join(names[1:])}]")
        else:
            out.append(f"{text} {names[0]}" if names else text)
    return out


def _entry(abc: ABC, names: Names, class_index: int | None, instruction: Instruction, side: str,
           numbers: dict[int, str]) -> tuple:
    targets = _targets(instruction)
    if targets:
        return ("op", instruction.op, [(side, id(t)) for t in targets])
    return ("op", long_form(_code_line(abc, names, instruction, class_index, numbers)), [])


def _debug_between(code: patterns.MethodCode, first: float | None, second: float | None) -> bool:
    """Whether a debug instruction lies between two item positions of the original (None: new code,
    which has none)."""
    if first is None or second is None:
        return False
    low, high = sorted((first, second))
    return any(not isinstance(code.items[k], Label) for k in range(int(low) + 1, int(high + 0.5)))
