"""Why a change can't be generated (yet): each function returns the reason, or None."""
from __future__ import annotations

import re

from swf_patch.abcfile import ABC, MULTINAME, MULTINAME_A, PRIVATE_NS, TRAIT_CONST
from swf_patch.errors import PatchError
from swf_patch.instructions import Instruction, decode
from swf_patch.operations import method_scope_depth
from swf_patch.listing import disassemble
from swf_patch.names import RUNTIME_NAME, UNTYPED

from .reading import signature, type_text
from .versions import ClassVersions

UNSUPPORTED_OPS = {"newfunction": "a nested function", "newactivation": "a nested function",
                   "newcatch": "try/catch", "newclass": "a class definition", "pushnamespace": "a namespace value"}
MODE_FLAGS = 0x01 | 0x02 | 0x04 | 0x40   # NEED_ARGUMENTS, NEED_ACTIVATION, NEED_REST, SET_DXNS


def changed_method_problem(versions: ClassVersions, member: str) -> str | None:
    """Why a changed method can't be patched (yet)."""
    original, exported, edited = versions.all
    if member not in original.methods or not original.abc.methods[original.methods[member]].body:
        return "the original file has no such method body"
    old, new = original.abc.methods[original.methods[member]], edited.abc.methods[edited.methods[member]]
    if old.body.exceptions or new.body.exceptions:
        return "try/catch isn't supported yet"
    for item in decode(new.body.code):
        if isinstance(item, Instruction) and item.op in UNSUPPORTED_OPS:
            return f"{UNSUPPORTED_OPS[item.op]} isn't supported yet"
    if signature(exported.abc, exported.methods[member]) != signature(edited.abc, edited.methods[member]):
        return "changing a method's parameters or return type isn't supported yet"
    if old.flags & MODE_FLAGS != new.flags & MODE_FLAGS:
        return "the new code uses `arguments`, rest parameters or nested scopes differently from the original"
    problem = code_problem(versions, edited.methods[member])
    if problem:
        return problem
    room = lambda body: body.max_scope_depth - body.init_scope_depth   # noqa: E731
    if room(new.body) > room(old.body):
        return "it needs more scope depth than the original (e.g. a `with` block)"
    return None


def new_method_problem(versions: ClassVersions, index: int, static: bool) -> str | None:
    """Why a new method (index, in the edited compile) can't be added with add_method (yet)."""
    abc, method = versions.edited.abc, versions.edited.abc.methods[index]
    if method.flags & MODE_FLAGS:
        return "new methods using `arguments`, rest parameters or nested scopes aren't supported yet"
    if type_text(abc, method.return_type) is None and method.return_type:
        return "this method's return type can't be written in a patch yet"
    try:                                              # add_method's rule for where it starts the method
        depth = method_scope_depth(versions.original.abc, versions.original.cls, static, versions.short)
    except PatchError:
        depth = None
    if depth != method.body.init_scope_depth:
        return "the class's methods don't agree on a scope depth to add this method at"
    return code_problem(versions, index)


def code_problem(versions: ClassVersions, index: int) -> str | None:
    """Why a method's code in the edited compile can't be written in a patch (yet)."""
    original, edited = versions.original, versions.edited
    body = edited.abc.methods[index].body
    if body.exceptions:
        return "try/catch isn't supported yet"
    written = constants_written(edited.abc, edited.cls, body)
    if written:
        return (f"FFDec compiled a setproperty on the constant {written}, which fails at run time "
                "(assigning a constant needs initproperty)")
    for item in decode(body.code):
        if isinstance(item, Instruction) and item.op in UNSUPPORTED_OPS:
            return f"{UNSUPPORTED_OPS[item.op]} isn't supported yet"
    lines = disassemble(edited.abc, index, names=edited.names)[1:]
    unwritable = sorted({edited.abc.name_of(int(n)) or f"#{n}" for line in lines for n in re.findall(r"\s#(\d+)", line)})
    if unwritable:
        return (f"it uses {', '.join(unwritable)}, which patch sets can't express yet "
                "(names private to a file, like a helper class declared outside the package block)")
    if any(UNTYPED in line or RUNTIME_NAME in line for line in lines):
        open_original = original.names.open_namespaces(original.cls)
        if open_original is None:
            return "it looks names up at run time, which this class's original code never does"
        if _namespace_set(original.abc, original.cls, open_original) != \
                _namespace_set(edited.abc, edited.cls, edited.names.open_namespaces(edited.cls)):
            return "it looks names up through different namespaces than the class's original code"
    return None


def constants_written(abc: ABC, class_index: int, body) -> str | None:
    """The name of a constant the code assigns with setproperty (a run-time error: only initproperty
    may), or None. Untyped names count when the class or a base class in the file has such a constant."""
    constant_names = {abc.multinames[t.name] for inst, cls in zip(abc.instances, abc.classes)
                      for t in inst.traits + cls.traits if t.base_kind == TRAIT_CONST}
    own_constants, current, seen = set(), class_index, set()
    while current is not None and current not in seen:          # the class and its bases in this file
        seen.add(current)
        own_constants |= {abc.name_of(t.name) for t in abc.instances[current].traits + abc.classes[current].traits
                          if t.base_kind == TRAIT_CONST}
        base = abc.instances[current].super_name
        current = next((i for i, inst in enumerate(abc.instances)
                        if base and abc.multinames[inst.name] == abc.multinames[base]), None)
    for item in decode(body.code):
        if isinstance(item, Instruction) and item.op == "setproperty":
            multiname = abc.multinames[item.args[0]]
            untyped = multiname[0] in (MULTINAME, MULTINAME_A) and abc.name_of(item.args[0]) in own_constants
            if multiname in constant_names or untyped:
                return abc.name_of(item.args[0])
    return None


def _namespace_set(abc: ABC, class_index: int, ns_set: int) -> list[str]:
    """A namespace set described independently of the file: namespaces by kind and name, private
    ones by whether they're the class's own (compilers name private namespaces differently)."""
    instance, cls = abc.instances[class_index], abc.classes[class_index]
    own = {abc.multinames[t.name][1] for t in instance.traits + cls.traits}
    described = []
    for index in abc.ns_sets[ns_set]:
        namespace = abc.namespaces[index]
        if namespace.kind == PRIVATE_NS:
            described.append(f"private ({'class' if index in own else 'file'})")
        else:
            described.append(f"{namespace.kind}:{abc.string(namespace.name)}")
    return sorted(described)
