"""Verifying a generated patch set before it's returned: it lints, applies, and gives every method
and member exactly what was intended, leaving every other method as it was."""
from __future__ import annotations

import os
import re
import tempfile

from swf_patch.abcfile import ABC, TRAIT_CONST
from swf_patch.engine import apply_to_abc
from swf_patch.errors import PatchError
from swf_patch.instructions import REGISTER_OPS
from swf_patch.lint import lint
from swf_patch.listing import disassemble
from swf_patch.names import Names
from swf_patch.patchset import PatchSets
from swf_patch.swf import read_abc

from .align import long_form, split_line
from .errors import GeneratorError
from .members import initializers
from .patches import NO_VALUE, FieldPatch, MethodPatch, NewMethodPatch, Patch, ValuesPatch
from .reading import constant, same, same_value, trait_value, type_text, visibility


def verify(data: bytes, text: str, after: ABC, names_after: Names, patches: list[Patch]) -> None:
    """The patch set lints and applies to data. Then: each method changed in place is exactly the
    original with the edits made; each method replaced or added is exactly FFDec's compile (after,
    with names_after); each new field and initial value is what the patch says; nothing else changed."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "generated.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        problems = lint([path])
        if problems:
            raise GeneratorError("generated patch set doesn't lint (a bug):\n  " + "\n  ".join(problems))
        sets = PatchSets([path])
    original, patched = ABC(read_abc(data)), ABC(read_abc(data))
    try:
        apply_to_abc(patched, sets, sets.values())
    except PatchError as e:
        hint = ""
        if re.search(r"no (public )?name", str(e)):
            hint = ("\n  FFDec's code uses a name the original file doesn't contain, and the generator didn't "
                    "declare it with add_names (a bug).")
        raise GeneratorError(f"the generated patch set can't be applied: {e}{hint}")
    names = Names(patched)
    replaced: set[int] = set()
    for patch in patches:
        if isinstance(patch, ValuesPatch):
            initializer = names.method(f"{patch.ref}.<static-init>")
            if patch.in_code:
                replaced.add(initializer)
            wrong = _wrong_value(patched, names, after, names_after, initializer, patch)
            if wrong:
                raise GeneratorError(f"{patch.ref}.{wrong}: the patched initial value differs from FFDec's (a bug)")
            continue
        if isinstance(patch, FieldPatch):
            if not _field_added(patched, names, patch):
                raise GeneratorError(f"{patch.ref}: the added field differs from FFDec's compile (a bug)")
            continue
        index = names.method(patch.ref)
        replaced.add(index)
        if isinstance(patch, NewMethodPatch) and not _signature_added(patched, index, patch):
            raise GeneratorError(f"{patch.ref}: the added method's signature differs from the patch (a bug)")
        if isinstance(patch, MethodPatch):
            if [long_form(line) for line in disassemble(patched, index, names=names)[1:]] != patch.expected:
                raise GeneratorError(f"{patch.ref}: the patched method isn't the original with the edits made (a bug)")
            continue
        compiled = _renumbered(disassemble(after, names_after.method(patch.ref), names=names_after)[1:], patch.registers)
        if _renumbered(disassemble(patched, index, names=names)[1:], {}) != compiled:
            raise GeneratorError(f"{patch.ref}: the patched method differs from FFDec's compile (a bug)")
    for index, (before, now) in enumerate(zip(original.methods, patched.methods)):
        if index not in replaced and (before.body and before.body.code) != (now.body and now.body.code):
            raise GeneratorError(f"method #{index} changed although it wasn't edited (a bug)")


def _wrong_value(patched: ABC, names: Names, after: ABC, names_after: Names, initializer: int,
                 patch: ValuesPatch) -> str | None:
    """The first member whose patched initial value isn't FFDec's, or None."""
    in_code = initializers(disassemble(patched, initializer, names=names)[1:])
    mine_class, theirs_class = names.find_class(patch.ref), names_after.find_class(patch.ref)
    for member, _, new in patch.values:
        if member in patch.in_code:
            if not (member in in_code and in_code[member][1] == new):
                return member
        elif not same_value(patched, names.trait(mine_class, member)[0], after, names_after.trait(theirs_class, member)[0]):
            return member
    return None


def _field_added(patched: ABC, names: Names, patch: FieldPatch) -> bool:
    """Its value is checked against the patch: it may come from FFDec's class initializer instead of
    the field FFDec compiled."""
    class_ref, _, member = patch.ref.rpartition(".")
    trait, static = names.trait(names.find_class(class_ref), member)
    return (static, visibility(patched, trait), type_text(patched, trait.type), trait.base_kind == TRAIT_CONST) == \
        (patch.static, patch.visibility, patch.type, patch.const) and same(trait_value(patched, trait), patch.value)


def _signature_added(abc: ABC, index: int, patch: NewMethodPatch) -> bool:
    """Whether an added method has the parameter types, defaults and return type the patch gives."""
    method = abc.methods[index]
    defaults = [constant(abc, i, k) for i, k in method.optional_values or []]
    wanted = [d for _, _, d in patch.params if d is not NO_VALUE]
    return ([type_text(abc, t) for t in method.param_types] == [t for _, t, _ in patch.params]
            and type_text(abc, method.return_type) == patch.returns
            and len(defaults) == len(wanted) and all(same(a, b) for a, b in zip(defaults, wanted)))


def _renumbered(lines: list[str], registers: dict[int, int]) -> list[str]:
    """Disassembled lines in long form with registers mapped (to compare FFDec's code with the
    patched method's, where its locals have other numbers)."""
    out = []
    for line in lines:
        op, operands = split_line(line)
        if op in REGISTER_OPS or op == "hasnext2":
            operands = ", ".join(str(registers.get(int(r), int(r))) for r in operands.split(","))
        out.append(f"{op} {operands}".strip() if not line.strip().endswith(":") else line.strip())
    return out
