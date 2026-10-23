"""Facts read from bytecode, as the generator compares and writes them: a class's members and
methods, types, initial values and visibility as patch sets write them."""
from __future__ import annotations

from typing import Any

from swf_patch.abcfile import (ABC, CONSTANT_DOUBLE, CONSTANT_FALSE, CONSTANT_INT, CONSTANT_NULL, CONSTANT_TRUE,
                               CONSTANT_UINT, CONSTANT_UTF8, PACKAGE_INTERNAL_NS, PACKAGE_NS, PRIVATE_NS, PROTECTED_NS,
                               QNAME, TYPENAME, Trait)
from swf_patch.errors import PatchError
from swf_patch.listing import class_name, method_names
from swf_patch.names import Names

from .patches import NO_VALUE, NULL

VISIBILITIES = {PRIVATE_NS: "private", PROTECTED_NS: "protected", PACKAGE_INTERNAL_NS: "internal"}


def members(abc: ABC, class_index: int) -> list[tuple]:
    """A class's members as (name, kind, static), sorted."""
    instance, cls = abc.instances[class_index], abc.classes[class_index]
    return sorted((abc.name_of(t.name), t.base_kind, static)
                  for static, traits in ((False, instance.traits), (True, cls.traits)) for t in traits)


def methods(abc: ABC, class_index: int) -> dict[str, int]:
    """member (as tools name it: name, get name, static name, <constructor>...) -> method index."""
    prefix = class_name(abc, class_index) + "."
    return {name[len(prefix):]: index for index, name in method_names(abc).items() if name.startswith(prefix)}


def find_trait(abc: ABC, class_index: int, name: str, kind: int, static: bool) -> Trait:
    traits = abc.classes[class_index].traits if static else abc.instances[class_index].traits
    return next(t for t in traits if abc.name_of(t.name) == name and t.base_kind == kind)


def class_ref(abc: ABC, names: Names, class_index: int) -> str:
    """A class as patch sets write it: its short name if that's unique in the file, else package::Class."""
    short = abc.class_name(class_index)
    try:
        if names.find_class(short) == class_index:
            return short
    except PatchError:
        pass
    return class_name(abc, class_index)


def visibility(abc: ABC, trait: Trait) -> str | None:
    """private, protected, internal or public, as add_field and add_method write it; None for others."""
    namespace = abc.namespaces[abc.multinames[trait.name][1]]
    if namespace.kind == PACKAGE_NS:
        return "public" if not abc.string(namespace.name) else None
    return VISIBILITIES.get(namespace.kind)


def type_text(abc: ABC, index: int) -> str | None:
    """A type as add_field and add_method write it (Class, package::Class, Vector.<T>, *), or None."""
    if not index:
        return "*"
    multiname = abc.multinames[index]
    if multiname[0] == TYPENAME:
        parts = [type_text(abc, multiname[1])] + [type_text(abc, p) for p in multiname[2]]
        return None if None in parts else f"{parts[0]}.<{', '.join(parts[1:])}>"
    if multiname[0] != QNAME or abc.namespaces[multiname[1]].kind != PACKAGE_NS:
        return None
    package, name = abc.string(abc.namespaces[multiname[1]].name), abc.string(multiname[2])
    return f"{package}::{name}" if package else name


def trait_value(abc: ABC, trait: Trait) -> Any:
    """A member's initial value (or, for a stand-in trait, a parameter's default): NO_VALUE, NULL, a
    Python value, or None if it can't be written."""
    if not trait.value_index:
        return NO_VALUE
    if trait.value_kind == CONSTANT_NULL:
        return NULL
    readers = {CONSTANT_TRUE: lambda i: True, CONSTANT_FALSE: lambda i: False, CONSTANT_UTF8: abc.string,
               CONSTANT_INT: lambda i: abc.ints[i], CONSTANT_UINT: lambda i: abc.uints[i],
               CONSTANT_DOUBLE: lambda i: abc.doubles[i]}
    reader = readers.get(trait.value_kind)
    return reader(trait.value_index) if reader else None


def constant(abc: ABC, index: int, kind: int) -> Any:
    """A parameter default (value index, kind), like trait_value."""
    return trait_value(abc, Trait(name=0, kind=0, value_index=index, value_kind=kind))


def same(a: Any, b: Any) -> bool:
    """Equal values; NaN equals NaN here."""
    return a == b or isinstance(a, float) and isinstance(b, float) and a != a and b != b


def same_value(abc_a: ABC, a: Trait, abc_b: ABC, b: Trait) -> bool:
    return same(trait_value(abc_a, a), trait_value(abc_b, b))


def signature(abc: ABC, index: int) -> tuple:
    """What patches never change about a method: its parameter and return types, how many defaults."""
    method = abc.methods[index]
    return (tuple(abc.name_of(p) for p in method.param_types), abc.name_of(method.return_type),
            len(method.optional_values or []))
