"""Turning edits of decompiled ActionScript into a patch set.

    export(data, classes, folder)   decompile classes of a SWF/GFX file into folder, to be edited
    generate(data, folder)          compile the original and the edited classes with FFDec, find what
                                    changed, and write a verified patch set making those changes

Comparing FFDec's compile of the original source with its compile of the edited source cancels out
FFDec's own way of compiling, leaving exactly the edits. Methods the edit left alone are stubbed in
both before compiling, so FFDec only compiles what was edited (its compiler fails on some code it
decompiles). A changed method is patched where it changed, anchored on the original instructions
around each change (methods.py, align.py), or else replaced whole, anchored on its entire original
code; either way the patch applies only where that code is as exported, and stops with a message
anywhere else. New members become add_field / add_method patches and changed initial values
set_initial_values (members.py), placed before the edits that may use them. The patch set is
verified before it's returned (verify.py).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass

import yaml

from ffdec_utils import FFDec, FFDecError
from swf_patch.abcfile import ABC
from swf_patch.errors import PatchError
from swf_patch.listing import class_name, disassemble
from swf_patch.names import Names
from swf_patch.swf import read_abc

from .checks import changed_method_problem
from .errors import GeneratorError
from .members import changed_declarations, declaration, initializers, new_members, static_init_values
from .methods import method_patch, probed_registers
from .patches import NO_VALUE, ORDER, FieldPatch, Patch, ValuesPatch
from .reading import class_ref, members
from .source import class_file, compilable, stub_unedited, with_register_probes
from .verify import verify
from .versions import ClassVersions, Version
from .writing import patch_set

MANIFEST = "patch_generator.yaml"
ORIGINALS = ".original"            # the exported sources, unedited, next to the editable copies


@dataclass
class Result:
    yaml: str                      # the patch set
    methods: list[str]             # what it adds or replaces (Class.member)


def export(data: bytes, classes: list[str], folder: str, ffdec: FFDec | None = None, source: str = "") -> list[str]:
    """Decompile classes (`Class` or `package::Class`) of a SWF/GFX file into folder: the files to
    edit, a copy of each under .original/, and a manifest. Returns the files to edit."""
    if os.path.exists(os.path.join(folder, MANIFEST)):
        raise GeneratorError(f"{folder} already holds exported classes; use another folder (or delete it)")
    abc = ABC(read_abc(data))
    names = Names(abc)
    try:
        qualified = [class_name(abc, names.find_class(c), ".") for c in classes]
    except PatchError as e:
        raise GeneratorError(str(e))
    ffdec = _ffdec(ffdec)
    with tempfile.TemporaryDirectory() as tmp:
        payload = os.path.join(tmp, "input.swf")
        with open(payload, "wb") as f:
            f.write(data)
        try:
            ffdec.decompile(payload, qualified, os.path.join(tmp, "out"))
        except FFDecError as e:
            raise GeneratorError(str(e))
        edit_files = []
        for name in qualified:
            exported = os.path.join(tmp, "out", "scripts", class_file(name))
            if not os.path.isfile(exported):
                raise GeneratorError(f"FFDec wrote no source for {name}")
            for target in (os.path.join(folder, class_file(name)), os.path.join(folder, ORIGINALS, class_file(name))):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(exported, target)
            edit_files.append(os.path.join(folder, class_file(name)))
    with open(os.path.join(folder, MANIFEST), "w", encoding="utf-8") as f:
        yaml.safe_dump({"source": source, "sha256": hashlib.sha256(data).hexdigest(), "classes": qualified}, f,
                       sort_keys=False)
    return edit_files


def generate(data: bytes, folder: str, ffdec: FFDec | None = None) -> Result:
    """The patch set for the edits in folder (from `export`), verified against data, the file the
    classes were exported from. Raises GeneratorError listing everything that can't be done."""
    with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
        raise GeneratorError("this isn't the file the classes were exported from")
    edited = {}
    for name in manifest["classes"]:
        original, current = (_read(os.path.join(root, class_file(name)))
                             for root in (os.path.join(folder, ORIGINALS), folder))
        if original != current:
            edited[name] = (original, current)
    if not edited:
        raise GeneratorError("no class has been edited")

    ffdec = _ffdec(ffdec)
    abc = ABC(read_abc(data))
    stubbed = {name: stub_unedited(*texts) for name, texts in edited.items()}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            before = _compile(ffdec, data, {name: texts[0] for name, texts in stubbed.items()}, os.path.join(tmp, "0"))
        except GeneratorError as e:
            raise GeneratorError(_uncompilable(ffdec, data, stubbed, tmp, e))
        try:
            after = _compile(ffdec, data, {name: texts[1] for name, texts in stubbed.items()}, os.path.join(tmp, "1"))
        except GeneratorError as e:
            raise GeneratorError(f"your edited source doesn't compile with FFDec: {e}")
        # Which register holds which variable, for naming locals after the source (see with_register_probes)
        probes = {name: with_register_probes(texts[1]) for name, texts in stubbed.items()}
        try:
            probed: ABC | None = _compile(ffdec, data, {name: p[0] for name, p in probes.items()}, os.path.join(tmp, "p"))
        except GeneratorError:
            probed = None                             # locals are named localN instead

    names = Names(abc), Names(before), Names(after)
    problems: list[str] = []
    patches = [patch for name, (original_text, text) in edited.items()
               for patch in _class_patches((abc, before, after), names, name, (original_text, text), problems,
                                           probed_registers(probed, name, probes[name][1]))]
    patches.sort(key=lambda patch: ORDER[type(patch)])
    if problems:
        raise GeneratorError("can't generate a patch set for these edits (yet):\n  " + "\n  ".join(problems))
    if not patches:
        raise GeneratorError("the edits don't change any method's code")
    text = patch_set(manifest, patches)
    verify(data, text, after, names[2], patches)
    return Result(text, [f"{p.ref}.{name}" for p in patches if isinstance(p, ValuesPatch) for name, _, _ in p.values]
                  + [p.ref for p in patches if not isinstance(p, ValuesPatch)])


def _ffdec(ffdec: FFDec | None) -> FFDec:
    try:
        return ffdec or FFDec()
    except FFDecError as e:
        raise GeneratorError(str(e))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _uncompilable(ffdec: FFDec, data: bytes, edited: dict[str, tuple[str, str]], tmp: str, error: GeneratorError) -> str:
    """The message for exported source FFDec can't compile back (with unedited methods stubbed):
    which classes (each compiled on its own when there are several)."""
    failing = list(edited)
    if len(edited) > 1:
        failing = []
        for n, (name, texts) in enumerate(edited.items()):
            try:
                _compile(ffdec, data, {name: texts[0]}, os.path.join(tmp, f"alone{n}"))
            except GeneratorError:
                failing.append(name)
    classes = ", ".join(name.rpartition(".")[2] for name in failing) or "the classes"
    return (f"FFDec can't compile its own decompilation of {classes} (in the methods you edited, or the "
            f"class's declarations), so this edit can't be generated yet (an FFDec limitation): {error}")


def _compile(ffdec: FFDec, data: bytes, sources: dict[str, str], tmp: str) -> ABC:
    os.makedirs(tmp)
    files = {}
    for name, text in sources.items():
        files[name] = os.path.join(tmp, class_file(name))
        os.makedirs(os.path.dirname(files[name]), exist_ok=True)
        with open(files[name], "w", encoding="utf-8") as f:
            f.write(compilable(text))
    payload, out = os.path.join(tmp, "input.swf"), os.path.join(tmp, "output.swf")
    with open(payload, "wb") as f:
        f.write(data)
    try:
        ffdec.compile(payload, out, files)
    except FFDecError as e:
        raise GeneratorError(str(e))
    with open(out, "rb") as f:
        return ABC(read_abc(f.read()))


def _class_patches(abcs: tuple[ABC, ABC, ABC], names: tuple[Names, Names, Names], qualified: str, texts: tuple[str, str],
                   problems: list[str], variables: dict[str, dict[int, str]]) -> list[Patch]:
    """Patches for one class's new members, changed initial values and changed methods. abcs and
    names: (original file, FFDec's compile of the exported source, FFDec's compile of the edited
    source); texts: the class's source, exported and edited."""
    package, _, short = qualified.rpartition(".")
    classes = [n.find_class(f"{package}::{short}" if package else short) for n in names]
    versions = ClassVersions(*(Version.of(abc, n, c) for abc, n, c in zip(abcs, names, classes)),
                             ref=class_ref(abcs[0], names[0], classes[0]), short=short,
                             exported_text=texts[0], edited_text=texts[1])
    original, exported, edited = versions.all
    before, after = members(exported.abc, exported.cls), members(edited.abc, edited.cls)
    removed = sorted({member[0] for member in before if member not in after})
    if removed:
        problems.append(f"{short}: removing or renaming members isn't supported yet ({', '.join(removed)})")
        return []
    patches: list[Patch] = new_members(versions, [m for m in after if m not in before], problems, variables)
    in_code = set(initializers(original.code("<static-init>")))
    values = changed_declarations(versions, [m for m in after if m in before], problems, in_code)
    # A new static field FFDec sets only in the class initializer gets that value in its add_field instead.
    set_in_code = initializers(edited.code("<static-init>"))
    for patch in patches:
        member = patch.ref.rpartition(".")[2]
        if isinstance(patch, FieldPatch) and patch.static and patch.value is NO_VALUE and member in set_in_code:
            patch.value = set_in_code[member][1]
    # Initializers the class initializer may gain without being replaced: those of the values patched anyway
    accepted = {p.ref.rpartition(".")[2]: p.value for p in patches if isinstance(p, FieldPatch) and p.static}
    accepted.update({name: new for name, _, new in (values.values if values else []) if new is not NO_VALUE})
    for member in edited.methods:
        if member not in exported.methods:
            continue                                  # a new method: added above
        if _listing(exported, member) == _listing(edited, member):
            continue
        if member == "<static-init>":
            # Changed numbers in member initializers (constants...) become set_initial_values, not a
            # replaced class initializer: much smaller, and the same operation a hand-written patch uses.
            changes = static_init_values(versions, accepted)
            if changes is not None:
                values = values or ValuesPatch(versions.ref, [])
                values.values += changes
                values.in_code |= {name for name, _, _ in changes}
                values.as3 = "\n".join(filter(None, [values.as3] + [declaration(versions.edited_text, n)
                                                                     for n, _, _ in changes]))
                continue
        problem = changed_method_problem(versions, member)
        if problem:
            problems.append(f"{short}.{member}: {problem}")
            continue
        patches.append(method_patch(versions, member, variables.get(member)))
    return patches + ([values] if values and values.values else [])


def _listing(version: Version, member: str) -> list[str]:
    """A method's whole disassembly, header (signature, limits) included: what tells whether it changed."""
    return disassemble(version.abc, version.methods[member], names=version.names)
