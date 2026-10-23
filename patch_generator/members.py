"""Patches for a class's members: new fields and methods (add_field, add_method), and initial values
changed, added or removed (set_initial_values), including values the class initializer sets."""
from __future__ import annotations

import difflib
import re
from typing import Any

from swf_patch.abcfile import ABC, TRAIT_CONST, TRAIT_GETTER, TRAIT_METHOD, TRAIT_SETTER, TRAIT_SLOT
from swf_patch.instructions import NUMBER_PUSHES
from swf_patch.listing import disassemble

from .align import split_line
from .checks import new_method_problem
from .methods import local_names_for, missing_names, name_registers
from .patches import NO_VALUE, FieldPatch, NewMethodPatch, Patch, ValuesPatch
from .reading import constant, find_trait, same_value, trait_value, type_text, visibility
from .source import method_source
from .versions import ClassVersions

METHOD_KINDS = {TRAIT_METHOD: "method", TRAIT_GETTER: "get", TRAIT_SETTER: "set"}


def new_members(versions: ClassVersions, added: list[tuple], problems: list[str],
                variables: dict[str, dict[int, str]]) -> list[Patch]:
    """add_field / add_method patches for the members the edit added: fields and constants; methods,
    getters and setters, static or not. added: (name, kind, static) of each."""
    abc, cls = versions.edited.abc, versions.edited.cls
    patches: list[Patch] = []
    for static, traits in ((False, abc.instances[cls].traits), (True, abc.classes[cls].traits)):
        for trait in traits:
            name = abc.name_of(trait.name)
            if (name, trait.base_kind, static) not in added:
                continue
            where = f"{versions.short}.{name}"
            access = visibility(abc, trait)
            if access is None:
                problems.append(f"{where}: new members must be private, protected, internal or public")
            elif trait.base_kind in (TRAIT_SLOT, TRAIT_CONST):
                field_type, value = type_text(abc, trait.type), trait_value(abc, trait)
                if field_type is None or value is None:
                    problems.append(f"{where}: this field's {'type' if field_type is None else 'initial value'} "
                                    "can't be written in a patch yet")
                else:
                    patches.append(FieldPatch(f"{versions.ref}.{name}", field_type, access, static, value,
                                              declaration(versions.edited_text, name),
                                              const=trait.base_kind == TRAIT_CONST))
            elif trait.base_kind in METHOD_KINDS:
                patch = _new_method(versions, trait, static, problems, variables, {m[0] for m in added})
                if patch:
                    patches.append(patch)
            else:
                problems.append(f"{where}: adding this kind of member isn't supported yet")
    return patches


def _new_method(versions: ClassVersions, trait, static: bool, problems: list[str],
                variables: dict[str, dict[int, str]], added_names: set[str]) -> NewMethodPatch | None:
    abc = versions.edited.abc
    name, kind = abc.name_of(trait.name), METHOD_KINDS[trait.base_kind]
    member = " ".join(filter(None, ["static" if static else "", "" if kind == "method" else kind, name]))
    problem = new_method_problem(versions, trait.method, static)
    source = method_source(versions.edited_text, versions.short, member)
    params = _params(abc, abc.methods[trait.method], source)
    if problem or params is None:
        problems.append(f"{versions.short}.{member}: "
                        f"{problem or 'a parameter type or default can not be written in a patch yet'}")
        return None
    param_names = {n + 1: p[0] for n, p in enumerate(params) if p[0]}
    local_names = local_names_for(abc, trait.method, variables.get(member))
    code = [name_registers(line, {**param_names, **local_names}).strip()
            for line in disassemble(abc, trait.method, names=versions.edited.names)[1:]]
    returns = type_text(abc, abc.methods[trait.method].return_type)
    return NewMethodPatch(f"{versions.ref}.{member}", visibility(abc, trait), returns or "*", code,
                          list(local_names.values()), source,
                          {r: r for r in local_names},                  # add_method numbers them in order
                          missing_names(versions, trait.method, added_names),
                          name=name, static=static, kind=kind, params=params)


def _params(abc: ABC, method, source: str | None) -> list[tuple[str | None, str, Any]] | None:
    """A new method's parameters as (name, type, default or NO_VALUE): names from the source's
    signature, types and defaults from the compile (a NaN default from the source: FFDec compiled
    the placeholder that stands in for it, see compilable). None if one can't be written."""
    declared = re.search(r"\(([^)]*)\)", source.split("{")[0]) if source else None
    parts = [p.strip() for p in declared.group(1).split(",")] if declared and declared.group(1).strip() else []
    defaults = method.optional_values or []
    first_default = len(method.param_types) - len(defaults)
    params = []
    for n, param_type in enumerate(method.param_types):
        part = parts[n] if n < len(parts) else ""
        name = re.match(r"\s*(\w+)", part)
        written_type = type_text(abc, param_type)
        default = NO_VALUE
        if n >= first_default:
            default = float("nan") if part.split("=", 1)[-1].strip() == "NaN" else \
                constant(abc, *defaults[n - first_default])
        if written_type is None or default is None:
            return None
        params.append((name.group(1) if name else None, written_type, default))
    return params


def changed_declarations(versions: ClassVersions, kept: list[tuple], problems: list[str],
                         in_code: set[str]) -> ValuesPatch | None:
    """A set_initial_values patch for members whose stored initial value the edit changed (added,
    removed); problems for other changes to a member's declaration (type, visibility), which would
    otherwise be lost. Static members the class initializer sets (in_code) are left to it: its code
    is what counts."""
    original, exported, edited = versions.all
    changed, declarations = [], []
    for name, kind, static in kept:
        if static and name in in_code and kind != TRAIT_METHOD:
            continue
        if kind not in (TRAIT_SLOT, TRAIT_CONST, TRAIT_METHOD):
            continue
        first, before, after = (find_trait(v.abc, v.cls, name, kind, static) for v in versions.all)
        if visibility(exported.abc, before) != visibility(edited.abc, after) or \
                (kind != TRAIT_METHOD and type_text(exported.abc, before.type) != type_text(edited.abc, after.type)):
            problems.append(f"{versions.short}.{name}: changing a member's type or visibility isn't supported yet")
            continue
        if kind == TRAIT_METHOD or same_value(exported.abc, before, edited.abc, after):
            continue
        old, new = trait_value(original.abc, first), trait_value(edited.abc, after)
        if old is None or new is None:
            problems.append(f"{versions.short}.{name}: this initial value can't be written in a patch yet")
            continue
        changed.append((name, old, new))
        declarations.append(declaration(versions.edited_text, name))
    return ValuesPatch(versions.ref, changed, "\n".join(filter(None, declarations)) or None) if changed else None


def initializers(lines: list[str]) -> dict[str, tuple[int, Any]]:
    """member -> (line of the push, number) for each `findproperty X; push N; initproperty|setproperty X`
    in disassembled code: how class initializers set members' initial values."""
    found: dict[str, tuple[int, Any]] = {}
    for i in range(1, len(lines) - 1):
        (find, target), (push, number), (assign, assigned) = (split_line(lines[k]) for k in (i - 1, i, i + 1))
        if find == "findproperty" and push in NUMBER_PUSHES and assign in ("initproperty", "setproperty") \
                and target == assigned:
            member = target.replace("untyped ", "").rpartition("::")[2].rpartition(".")[2]
            found.setdefault(member, (i, float(number) if re.search(r"[.eEn]", number) else int(number)))
    return found


def static_init_values(versions: ClassVersions, accepted: dict[str, Any]) -> list[tuple] | None:
    """When the edit's only changes to the class initializer are numbers in member initializers, and
    initializers of members whose value is patched anyway (accepted: member -> value), those changes
    as (member, original value, new value); otherwise None (the initializer is replaced whole)."""
    lines = [v.code("<static-init>") for v in versions.all]
    original, old, new = (initializers(code) for code in lines)
    at_line = {line: member for member, (line, _) in new.items()}
    changes = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, lines[1], lines[2], autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        member = at_line.get(j1)
        if tag == "replace" and i2 - i1 == j2 - j1 == 1 and member in original and old.get(member, (None,))[0] == i1:
            changes.append((member, original[member][1], new[member][1]))
            continue
        inserted = [at_line[k] for k in range(j1, j2) if k in at_line]
        if tag == "insert" and len(inserted) * 3 == j2 - j1 and \
                all(accepted.get(m, NO_VALUE) == new[m][1] for m in inserted):
            continue
        return None
    return changes


def declaration(text: str, member: str) -> str | None:
    """A member's declaration line in the source, for the patch's as3."""
    found = re.search(rf"^[^\n]*\b(?:var|const)\s+{re.escape(member)}\b[^\n]*$", text, re.MULTILINE)
    return found.group(0).strip() if found else None
