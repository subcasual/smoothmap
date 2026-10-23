"""Human-readable views of bytecode, for tools: qualified names and disassembly.

Disassembly uses the patch sets' assembly syntax: a pattern can be copied from it, and assembling
it in the same file gives back the same code (operands the syntax can't express are shown as #n).
Labels are numbered L0, L1, ... in order of appearance within each method, so two versions of a
method can be compared line by line.
"""
from __future__ import annotations

from .abcfile import ABC, TRAIT_CONST, TRAIT_GETTER, TRAIT_METHOD, TRAIT_SETTER, TRAIT_SLOT
from .assembler import quote
from .instructions import CALL_OPS, NAME_OPS, Instruction, Label, decode
from .names import Names

TRAIT_KIND_WORDS = {TRAIT_SLOT: "var", TRAIT_METHOD: "function", TRAIT_GETTER: "get", TRAIT_SETTER: "set",
                    TRAIT_CONST: "const"}


# --------------------------------------------------------------------------- names

def class_name(abc: ABC, class_index: int, separator: str = "::") -> str:
    """`package::Class` (`Class` in the top-level package)."""
    package, name = abc.class_package(class_index), abc.class_name(class_index)
    return f"{package}{separator}{name}" if package else name


def method_names(abc: ABC) -> dict[int, str]:
    """Method index -> `package::Class.method`, `...Class.<constructor>`, `...Class.<static-init>`,
    with `static `, `get ` or `set ` before the method name where it applies. Methods that no class
    member names (closures, script initializers) are `method #<index>`."""
    names = {index: f"method #{index}" for index in range(len(abc.methods))}
    for i, instance in enumerate(abc.instances):
        cls = class_name(abc, i)
        names[instance.constructor] = f"{cls}.<constructor>"
        names[abc.classes[i].static_init] = f"{cls}.<static-init>"
        for static, traits in (("", instance.traits), ("static ", abc.classes[i].traits)):
            for trait in traits:
                if trait.method is not None:
                    accessor = {TRAIT_GETTER: "get ", TRAIT_SETTER: "set "}.get(trait.base_kind, "")
                    names[trait.method] = f"{cls}.{static}{accessor}{abc.name_of(trait.name)}"
    return names


def field_types(abc: ABC) -> dict[str, str]:
    """`package::Class.field` (or `...Class.static field`) -> its declared type's name, for every
    var and const."""
    fields = {}
    for i, instance in enumerate(abc.instances):
        cls = class_name(abc, i)
        for prefix, traits in (("", instance.traits), ("static ", abc.classes[i].traits)):
            for trait in traits:
                if trait.base_kind in (TRAIT_SLOT, TRAIT_CONST):
                    fields[f"{cls}.{prefix}{abc.name_of(trait.name)}"] = abc.name_of(trait.type) if trait.type else "*"
    return fields


# --------------------------------------------------------------------------- disassembly

def disassemble(abc: ABC, method_index: int, keep_debug: bool = False, names: Names | None = None) -> list[str]:
    """A method as assembly lines, after a header line with its signature and limits. Pass one
    Names when disassembling many methods of a file (it caches what operands need)."""
    names = names or Names(abc)
    context = names.class_of_method(method_index)
    method = abc.methods[method_index]
    params = ", ".join(abc.name_of(p) or "*" for p in method.param_types)
    body = method.body
    if not body:
        return [f"; params ({params})  (no body)"]
    lines = [f"; params ({params})  locals={body.local_count}  maxstack={body.max_stack}"]
    items = decode(body.code)
    label_names = {label: f"L{n}" for n, label in enumerate(i for i in items if isinstance(i, Label))}
    for item in items:
        if isinstance(item, Label):
            lines.append(f"  {label_names[item]}:")
        elif keep_debug or not item.op.startswith("debug"):
            lines.append(f"    {item.op:<16}{format_operands(abc, item, label_names, names, context)}".rstrip())
    return lines


def format_operands(abc: ABC, instruction: Instruction, label_names: dict[Label, str] | None = None,
                    names: Names | None = None, context: int | None = None) -> str:
    """An instruction's operands as written in assembly: names, numbers, strings, labels.
    context: the class whose code it is (for names looked up through its namespaces)."""
    op, args = instruction.op, instruction.args
    names = names or Names(abc)

    def name(index: int) -> str:
        return names.text(index, context) or f"#{index}"    # #n: a name the syntax can't express

    def label(target: Label) -> str:
        return (label_names or {}).get(target, target.name)

    if op in NAME_OPS:
        return name(args[0])
    if op in CALL_OPS:
        return f"{name(args[0])}, {args[1]}"
    if op == "pushint":
        return str(abc.ints[args[0]])
    if op == "pushuint":
        return str(abc.uints[args[0]])
    if op == "pushdouble":
        return repr(abc.doubles[args[0]])
    if op == "pushstring":
        return quote(abc.string(args[0]))
    if op == "lookupswitch":
        return f"{label(args[0])}, [{', '.join(label(case) for case in args[1])}]"
    return ", ".join(label(a) if isinstance(a, Label) else str(a) for a in args)
