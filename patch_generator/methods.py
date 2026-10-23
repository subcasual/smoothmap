"""Patches for changed methods: anchored on the original instructions around each change where
align.py can place it, else replacing the whole method; and what both need: names for new locals,
names the code needs the file to get."""
from __future__ import annotations

import difflib
import json

from swf_patch.abcfile import ABC, NAMESPACE, PACKAGE_NS, QNAME, TYPENAME
from swf_patch.errors import PatchError
from swf_patch.instructions import CALL_OPS, NAME_OPS, REGISTER_OPS, SHORT_REGISTER_OPS, Instruction, decode
from swf_patch.names import AS3_NAMESPACE, Names

from .align import pattern_line, place, split_line
from .patches import MethodPatch, WholeMethodPatch
from .reading import members, methods
from .source import method_source
from .versions import ClassVersions


def method_patch(versions: ClassVersions, member: str, variables: dict[int, str] | None
                 ) -> MethodPatch | WholeMethodPatch:
    """The patch for a changed method: steps changing just the edited instructions where they can be
    placed in the original (see align), else the whole method replaced. variables: register ->
    source name."""
    original, edited = versions.original, versions.edited
    index = edited.methods[member]
    ref = f"{versions.ref}.{member}"
    source = method_source(versions.edited_text, versions.short, member)
    new_members = ({m[0] for m in members(edited.abc, edited.cls)}
                   - {m[0] for m in members(versions.exported.abc, versions.exported.cls)})
    local_names = local_names_for(edited.abc, index, variables)
    placement = place(versions, member, local_names)
    if placement is not None:
        new_locals = sorted(placement.new_locals, key=placement.new_locals.get)
        return MethodPatch(ref, placement.steps, placement.expected, [local_names[r] for r in new_locals],
                           _excerpt(method_source(versions.exported_text, versions.short, member), source),
                           missing_names(versions, index, new_members, placement.instructions))
    # Every local (beyond the parameters) becomes a named local of the edit, named after the source's
    # variables: the edit gives them new registers after the original method's, in order, and debug names.
    first_new = original.abc.methods[original.methods[member]].body.local_count
    # One indentation for every line: in a YAML block, a shallower label line would end the block.
    code = [name_registers(line, local_names).strip() for line in edited.code(member)]
    return WholeMethodPatch(ref, _pattern(original.abc, original.methods[member]), code, list(local_names.values()),
                            source, {r: first_new + n for n, r in enumerate(local_names)},
                            missing_names(versions, index, new_members))


def _excerpt(original: str | None, edited: str | None, context: int = 2) -> str | None:
    """The edited method's changed lines with a little context, after its signature: what an
    anchored patch changes, in ActionScript."""
    if not original or not edited:
        return edited
    old, new = original.splitlines(), edited.splitlines()
    shown: set[int] = set()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            shown |= set(range(max(j1 - context, 0), min(max(j2, j1 + 1) + context, len(new))))
    if not shown:
        return edited
    out, previous = [new[0]], 0
    for number in sorted(shown - {0}):
        if number != previous + 1:
            out.append("   ...")
        out.append(new[number])
        previous = number
    if previous != len(new) - 1:
        out.append("   ...")
    return "\n".join(out)


def _pattern(abc: ABC, index: int) -> list[str]:
    """A method's code as a pattern matching exactly it (every instruction, debug aside)."""
    return [pattern_line(abc, item) for item in decode(abc.methods[index].body.code)
            if isinstance(item, Instruction) and not item.op.startswith("debug")]


def missing_names(versions: ClassVersions, index: int, new_members: set[str],
                  instructions: list | None = None) -> list[str]:
    """The qualified names the edited compile's code for a method (or just these of its
    instructions) uses that the original file doesn't have (an add_names declares them). Class
    members aren't among them: they exist, or a patch adds them."""
    abc, original = versions.edited.abc, versions.original.abc
    missing: list[str] = []

    def qnames(multiname: int) -> list[int]:
        kind, *fields = abc.multinames[multiname] or (None,)
        if kind == QNAME:
            return [multiname]
        if kind == TYPENAME:
            return qnames(fields[0]) + [q for p in fields[1] for q in qnames(p)]
        return []

    for item in instructions if instructions is not None else decode(abc.methods[index].body.code):
        if not (isinstance(item, Instruction) and (item.op in NAME_OPS or item.op in CALL_OPS)):
            continue
        for multiname in qnames(item.args[0]):
            _, namespace_index, name_index = abc.multinames[multiname]
            namespace, name = abc.namespaces[namespace_index], abc.string(name_index)
            if namespace.kind not in (PACKAGE_NS, NAMESPACE) or name in new_members:
                continue
            uri = abc.string(namespace.name)
            try:
                original.find_qname(uri, name, namespace.kind)
            except LookupError:
                qualifier = ("public" if not uri else uri) if namespace.kind == PACKAGE_NS else \
                    ("AS3" if uri == AS3_NAMESPACE else json.dumps(uri))
                if f"{qualifier}::{name}" not in missing:
                    missing.append(f"{qualifier}::{name}")
    return missing


def local_names_for(abc: ABC, index: int, variables: dict[int, str] | None) -> dict[int, str]:
    """register -> name for a method's locals (the registers after `this` and the parameters): the
    source's variable names where the probe found their registers, localN for the rest (FFDec's own
    temporaries, like loop iterators)."""
    method = abc.methods[index]
    names: dict[int, str] = {}
    for register in range(1 + len(method.param_types), method.body.local_count):
        name = (variables or {}).get(register, f"local{register}")
        while name in names.values():
            name += "_"
        names[register] = name
    return names


def probed_registers(probed: ABC | None, qualified: str, declared: dict[str, list[str]]) -> dict[str, dict[int, str]]:
    """member -> {register: variable name}, read from the probe compile: each probed method starts
    with trace(a, b, ...), which reads its variables' registers in order (see with_register_probes)."""
    if probed is None:
        return {}
    package, _, short = qualified.rpartition(".")
    try:
        class_index = Names(probed).find_class(f"{package}::{short}" if package else short)
    except PatchError:
        return {}
    found, compiled = {}, methods(probed, class_index)
    for key, variables in declared.items():
        member = "<constructor>" if key == short else key
        if member not in compiled:
            continue
        reads: list[int] = []
        started = False
        for item in decode(probed.methods[compiled[member]].body.code):
            if not isinstance(item, Instruction):
                continue
            if not started:
                started = item.op in ("findpropstrict", "getlex") and probed.name_of(item.args[0]) == "trace"
                continue
            if item.op in SHORT_REGISTER_OPS and SHORT_REGISTER_OPS[item.op][0] == "getlocal":
                reads.append(SHORT_REGISTER_OPS[item.op][1])
            elif item.op == "getlocal":
                reads.append(item.args[0])
            else:
                break
        if len(reads) == len(variables):
            found[member] = dict(zip(reads, variables))
    return found


def name_registers(line: str, local_names: dict[int, str]) -> str:
    """A disassembled line with its registers that are named locals written by their names
    (getlocal1 etc., which build the register into the instruction, become getlocal <name>)."""
    op, operands = split_line(line)
    if op not in REGISTER_OPS and op != "hasnext2":
        return line
    registers = [r.strip() for r in operands.split(",")]
    named = [local_names.get(int(r), r) if r.isdecimal() else r for r in registers]
    return f"{op:<16}{', '.join(named)}" if named != registers else line
