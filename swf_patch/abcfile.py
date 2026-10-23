"""The ABC file (ActionScript Byte Code, as in a SWF's DoABC tag): reading, editing, writing.

Follows "ActionScript Virtual Machine 2 (AVM2) Overview", chapter 4. An ABC file is a set of
constant pools (integers, doubles, strings, namespaces, multinames) followed by method signatures,
classes and method bodies, all of which refer to each other and to the pools by index.
Reading then writing a file reproduces it byte for byte.

Pools keep the format's convention that index 0 is an implicit "none" entry; real entries
start at 1. Multinames are tuples whose first element is the kind (see `name_of`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import NamedTuple, Optional

from .bytestream import Reader, Writer

# Namespace kinds
PRIVATE_NS = 0x05
NAMESPACE = 0x08
PACKAGE_NS = 0x16                 # public names, and a package's public names
PACKAGE_INTERNAL_NS = 0x17
PROTECTED_NS = 0x18
EXPLICIT_NS = 0x19
STATIC_PROTECTED_NS = 0x1A

# Multiname kinds. QNames name something in one namespace; Multinames search a namespace set;
# RTQNames and *L kinds take their namespace and/or name from the stack at run time.
QNAME, QNAME_A = 0x07, 0x0D                       # (kind, namespace, name)
MULTINAME, MULTINAME_A = 0x09, 0x0E               # (kind, name, namespace set)
RTQNAME, RTQNAME_A = 0x0F, 0x10                   # (kind, name)
RTQNAME_L, RTQNAME_LA = 0x11, 0x12                # (kind,)
MULTINAME_L, MULTINAME_LA = 0x1B, 0x1C            # (kind, namespace set)
TYPENAME = 0x1D                                   # (kind, name, [type parameters]), e.g. Vector.<int>

# Constant kinds: a trait's initial value (Trait.value_kind) and an optional parameter's default
CONSTANT_UNDEFINED, CONSTANT_UTF8, CONSTANT_INT, CONSTANT_UINT, CONSTANT_DOUBLE = 0x00, 0x01, 0x03, 0x04, 0x06
CONSTANT_FALSE, CONSTANT_TRUE, CONSTANT_NULL = 0x0A, 0x0B, 0x0C

# Trait kinds (low 4 bits of Trait.kind) and attributes (high 4 bits)
TRAIT_SLOT, TRAIT_METHOD, TRAIT_GETTER, TRAIT_SETTER, TRAIT_CLASS, TRAIT_FUNCTION, TRAIT_CONST = range(7)
TRAIT_HAS_METADATA = 0x40

METHOD_HAS_OPTIONAL = 0x08        # MethodInfo.flags
METHOD_HAS_PARAM_NAMES = 0x80
CLASS_PROTECTED_NS = 0x08         # Instance.flags


class Namespace(NamedTuple):
    kind: int
    name: int                     # string index


@dataclass
class Trait:
    """A member of a class, script or activation: which fields are used depends on the kind."""
    name: int                                     # multiname index (a QName)
    kind: int                                     # TRAIT_* | attributes
    slot: int = 0                                 # slot, const, class and function traits
    type: int = 0                                 # slot/const: declared type (multiname, 0 = any)
    value_index: int = 0                          # slot/const: constant initializer (0 = none)
    value_kind: Optional[int] = None              # its pool, when value_index is set
    disp: int = 0                                 # method/getter/setter: dispatch id
    method: Optional[int] = None                  # method/getter/setter/function: method index
    cls: Optional[int] = None                     # class trait: class index
    metadata: Optional[list[int]] = None          # with TRAIT_HAS_METADATA

    @property
    def base_kind(self) -> int:
        return self.kind & 0x0F


@dataclass
class ExceptionHandler:
    start: int                                    # byte offsets in the method body
    end: int
    target: int
    exception_type: int                           # multiname indices
    variable_name: int


@dataclass
class MethodBody:
    method: int                                   # index of the MethodInfo it belongs to
    max_stack: int
    local_count: int                              # registers, including `this` and the parameters
    init_scope_depth: int
    max_scope_depth: int
    code: bytes
    exceptions: list[ExceptionHandler] = field(default_factory=list)
    traits: list[Trait] = field(default_factory=list)


@dataclass
class MethodInfo:
    """A method's signature; its code is in `body` (None for interface and native methods)."""
    param_types: list[int]                        # multiname indices (0 = any)
    return_type: int
    name: int                                     # string index
    flags: int
    optional_values: Optional[list[tuple[int, int]]] = None    # (value index, kind), with METHOD_HAS_OPTIONAL
    param_names: Optional[list[int]] = None       # with METHOD_HAS_PARAM_NAMES
    body: Optional[MethodBody] = None


@dataclass
class Metadata:
    name: int                                     # string indices
    keys: list[int]
    values: list[int]


@dataclass
class Instance:
    """A class's instance side: name, superclass, constructor and instance members."""
    name: int                                     # multiname index
    super_name: int
    flags: int
    protected_ns: Optional[int]                   # with CLASS_PROTECTED_NS
    interfaces: list[int]
    constructor: int                              # method index
    traits: list[Trait]


@dataclass
class ClassInfo:
    """A class's static side. Pairs with the Instance at the same index."""
    static_init: int                              # method index
    traits: list[Trait]


@dataclass
class Script:
    init: int                                     # method index
    traits: list[Trait]


class ABC:
    def __init__(self, data: bytes) -> None:
        reader = Reader(data)
        self.minor_version = reader.u16()
        self.major_version = reader.u16()

        self.ints: list[int] = [0]
        # The original encoding of each int: some compilers write non-minimal s32 encodings,
        # and writing them back as read keeps files byte-identical.
        self._int_encodings: list[bytes | None] = [None]
        for _ in range(_pool_size(reader)):
            start = reader.pos
            self.ints.append(reader.s32())
            self._int_encodings.append(bytes(data[start:reader.pos]))
        self.uints: list[int] = [0] + [reader.u30() for _ in range(_pool_size(reader))]
        self.doubles: list[float | None] = [None] + [reader.d64() for _ in range(_pool_size(reader))]
        self.strings: list[bytes | None] = [None] + [reader.raw(reader.u30()) for _ in range(_pool_size(reader))]
        self.namespaces: list[Namespace | None] = [None] + [
            Namespace(reader.u8(), reader.u30()) for _ in range(_pool_size(reader))]
        self.ns_sets: list[list[int] | None] = [None] + [
            [reader.u30() for _ in range(reader.u30())] for _ in range(_pool_size(reader))]
        self.multinames: list[tuple | None] = [None] + [_read_multiname(reader) for _ in range(_pool_size(reader))]
        self._qname_index: dict[tuple[int, str, str], int] = {}    # for find_qname
        self._qnames_indexed = 1

        self.methods = [_read_method_info(reader) for _ in range(reader.u30())]
        self.metadata = [_read_metadata(reader) for _ in range(reader.u30())]
        class_count = reader.u30()
        self.instances = [_read_instance(reader) for _ in range(class_count)]
        self.classes = [ClassInfo(reader.u30(), _read_traits(reader)) for _ in range(class_count)]
        self.scripts = [Script(reader.u30(), _read_traits(reader)) for _ in range(reader.u30())]

        # Bodies are stored separately from their MethodInfo; keep their order for writing.
        self.body_order: list[int] = []
        for _ in range(reader.u30()):
            body = _read_method_body(reader)
            self.methods[body.method].body = body
            self.body_order.append(body.method)
        if reader.pos != len(data):
            raise ValueError("trailing bytes after the ABC data")

    # ----------------------------------------------------------------------- writing

    def serialize(self) -> bytes:
        writer = Writer()
        writer.u16(self.minor_version)
        writer.u16(self.major_version)

        _write_pool_size(writer, self.ints)
        for index, value in enumerate(self.ints[1:], 1):
            encoding = self._int_encodings[index] if index < len(self._int_encodings) else None
            if encoding is not None and Reader(encoding).s32() == value:      # unchanged since reading
                writer.raw(encoding)
            else:
                writer.s32(value)
        _write_pool_size(writer, self.uints)
        for value in self.uints[1:]:
            writer.u30(value)
        _write_pool_size(writer, self.doubles)
        for value in self.doubles[1:]:
            writer.d64(value)
        _write_pool_size(writer, self.strings)
        for value in self.strings[1:]:
            writer.u30(len(value))
            writer.raw(value)
        _write_pool_size(writer, self.namespaces)
        for kind, name in self.namespaces[1:]:
            writer.u8(kind)
            writer.u30(name)
        _write_pool_size(writer, self.ns_sets)
        for ns_set in self.ns_sets[1:]:
            _write_u30_list(writer, ns_set)
        _write_pool_size(writer, self.multinames)
        for multiname in self.multinames[1:]:
            _write_multiname(writer, multiname)

        writer.u30(len(self.methods))
        for method in self.methods:
            _write_method_info(writer, method)
        writer.u30(len(self.metadata))
        for entry in self.metadata:
            writer.u30(entry.name)
            writer.u30(len(entry.keys))
            for index in entry.keys + entry.values:
                writer.u30(index)
        writer.u30(len(self.instances))
        for instance in self.instances:
            _write_instance(writer, instance)
        for cls in self.classes:
            writer.u30(cls.static_init)
            _write_traits(writer, cls.traits)
        writer.u30(len(self.scripts))
        for script in self.scripts:
            writer.u30(script.init)
            _write_traits(writer, script.traits)
        writer.u30(len(self.body_order))
        for method_index in self.body_order:
            _write_method_body(writer, self.methods[method_index].body)
        return bytes(writer.data)

    # ----------------------------------------------------------------------- reading the pools

    def string(self, index: int) -> str:
        return self.strings[index].decode("utf8") if index else ""

    def name_of(self, multiname: int) -> str | None:
        """The name part of a multiname, or None for index 0 (the any type, `*`) and for kinds
        whose name comes from the stack."""
        if not multiname:
            return None
        kind, *fields = self.multinames[multiname]
        if kind in (QNAME, QNAME_A):
            return self.string(fields[1])
        if kind in (MULTINAME, MULTINAME_A, RTQNAME, RTQNAME_A):
            return self.string(fields[0])
        return None

    def namespace_name(self, namespace: int) -> str:
        return self.string(self.namespaces[namespace].name)

    def class_name(self, class_index: int) -> str:
        return self.name_of(self.instances[class_index].name)

    def class_package(self, class_index: int) -> str:
        _, namespace, _ = self.multinames[self.instances[class_index].name]
        return self.namespace_name(namespace)

    def find_namespace(self, name: str, kind: int = PACKAGE_NS) -> int:
        """Index of an existing namespace (default: the public namespace of package `name`)."""
        for index, namespace in enumerate(self.namespaces):
            if namespace and namespace.kind == kind and self.string(namespace.name) == name:
                return index
        raise LookupError(f"namespace {name!r} kind {kind:#x}")

    def find_qname(self, namespace_name: str, name: str, namespace_kind: int = PACKAGE_NS) -> int:
        """Index of an existing QName, e.g. ("flash.utils", "getTimer"): the first, if several."""
        # Pool entries are only ever appended, so the index catches up with new ones as needed.
        for i in range(self._qnames_indexed, len(self.multinames)):
            multiname = self.multinames[i]
            if multiname[0] == QNAME:
                namespace = self.namespaces[multiname[1]]
                self._qname_index.setdefault((namespace.kind, self.string(namespace.name), self.string(multiname[2])), i)
        self._qnames_indexed = len(self.multinames)
        try:
            return self._qname_index[(namespace_kind, namespace_name, name)]
        except KeyError:
            raise LookupError(f"QName {namespace_name}::{name}") from None

    # ----------------------------------------------------------------------- adding to the pools

    def add_string(self, value: str) -> int:
        """Index of the string, appended if it isn't in the pool yet."""
        return _index_or_append(self.strings, value.encode("utf8"))

    def add_int(self, value: int) -> int:
        return _index_or_append(self.ints, value)

    def add_uint(self, value: int) -> int:
        return _index_or_append(self.uints, value)

    def add_double(self, value: float) -> int:
        """Index of the double, appended if it isn't in the pool yet. Compared as stored, so NaN
        finds NaN and -0.0 doesn't find 0.0."""
        packed = struct.pack("<d", value)
        for index, existing in enumerate(self.doubles[1:], 1):
            if struct.pack("<d", existing) == packed:
                return index
        self.doubles.append(float(value))
        return len(self.doubles) - 1

    def add_multiname(self, multiname: tuple) -> int:
        return _index_or_append(self.multinames, multiname)

    def add_namespace(self, name: str, kind: int = PACKAGE_NS) -> int:
        return _index_or_append(self.namespaces, Namespace(kind, self.add_string(name)))


def _index_or_append(pool: list, value: object) -> int:
    if value in pool[1:]:
        return pool.index(value, 1)
    pool.append(value)
    return len(pool) - 1


# --------------------------------------------------------------------------- reading helpers

def _pool_size(reader: Reader) -> int:
    """Number of real entries in a pool (the stored count includes the implicit entry 0)."""
    return max(0, reader.u30() - 1)


def _read_multiname(reader: Reader) -> tuple:
    kind = reader.u8()
    if kind in (QNAME, QNAME_A, MULTINAME, MULTINAME_A):
        return kind, reader.u30(), reader.u30()
    if kind in (RTQNAME, RTQNAME_A, MULTINAME_L, MULTINAME_LA):
        return kind, reader.u30()
    if kind in (RTQNAME_L, RTQNAME_LA):
        return (kind,)
    if kind == TYPENAME:
        name = reader.u30()
        return kind, name, [reader.u30() for _ in range(reader.u30())]
    raise ValueError(f"unknown multiname kind {kind:#x}")


def _read_method_info(reader: Reader) -> MethodInfo:
    param_count = reader.u30()
    return_type = reader.u30()
    param_types = [reader.u30() for _ in range(param_count)]
    method = MethodInfo(param_types, return_type, name=reader.u30(), flags=reader.u8())
    if method.flags & METHOD_HAS_OPTIONAL:
        method.optional_values = [(reader.u30(), reader.u8()) for _ in range(reader.u30())]
    if method.flags & METHOD_HAS_PARAM_NAMES:
        method.param_names = [reader.u30() for _ in range(param_count)]
    return method


def _read_metadata(reader: Reader) -> Metadata:
    name = reader.u30()
    count = reader.u30()
    keys = [reader.u30() for _ in range(count)]
    values = [reader.u30() for _ in range(count)]
    return Metadata(name, keys, values)


def _read_instance(reader: Reader) -> Instance:
    name, super_name, flags = reader.u30(), reader.u30(), reader.u8()
    protected_ns = reader.u30() if flags & CLASS_PROTECTED_NS else None
    interfaces = [reader.u30() for _ in range(reader.u30())]
    return Instance(name, super_name, flags, protected_ns, interfaces,
                    constructor=reader.u30(), traits=_read_traits(reader))


def _read_traits(reader: Reader) -> list[Trait]:
    traits = []
    for _ in range(reader.u30()):
        trait = Trait(name=reader.u30(), kind=reader.u8())
        if trait.base_kind in (TRAIT_SLOT, TRAIT_CONST):
            trait.slot, trait.type, trait.value_index = reader.u30(), reader.u30(), reader.u30()
            if trait.value_index:
                trait.value_kind = reader.u8()
        elif trait.base_kind in (TRAIT_METHOD, TRAIT_GETTER, TRAIT_SETTER):
            trait.disp, trait.method = reader.u30(), reader.u30()
        elif trait.base_kind == TRAIT_CLASS:
            trait.slot, trait.cls = reader.u30(), reader.u30()
        elif trait.base_kind == TRAIT_FUNCTION:
            trait.slot, trait.method = reader.u30(), reader.u30()
        else:
            raise ValueError(f"unknown trait kind {trait.base_kind}")
        if trait.kind & TRAIT_HAS_METADATA:
            trait.metadata = [reader.u30() for _ in range(reader.u30())]
        traits.append(trait)
    return traits


def _read_method_body(reader: Reader) -> MethodBody:
    body = MethodBody(method=reader.u30(), max_stack=reader.u30(), local_count=reader.u30(),
                      init_scope_depth=reader.u30(), max_scope_depth=reader.u30(),
                      code=reader.raw(reader.u30()))
    body.exceptions = [ExceptionHandler(*(reader.u30() for _ in range(5))) for _ in range(reader.u30())]
    body.traits = _read_traits(reader)
    return body


# --------------------------------------------------------------------------- writing helpers

def _write_pool_size(writer: Writer, pool: list) -> None:
    writer.u30(len(pool) if len(pool) > 1 else 0)


def _write_u30_list(writer: Writer, values: list[int]) -> None:
    writer.u30(len(values))
    for value in values:
        writer.u30(value)


def _write_multiname(writer: Writer, multiname: tuple) -> None:
    kind, *fields = multiname
    writer.u8(kind)
    if kind == TYPENAME:
        name, type_params = fields
        writer.u30(name)
        _write_u30_list(writer, type_params)
    else:
        for index in fields:
            writer.u30(index)


def _write_method_info(writer: Writer, method: MethodInfo) -> None:
    writer.u30(len(method.param_types))
    writer.u30(method.return_type)
    for param_type in method.param_types:
        writer.u30(param_type)
    writer.u30(method.name)
    writer.u8(method.flags)
    if method.flags & METHOD_HAS_OPTIONAL:
        writer.u30(len(method.optional_values))
        for value, kind in method.optional_values:
            writer.u30(value)
            writer.u8(kind)
    if method.flags & METHOD_HAS_PARAM_NAMES:
        for name in method.param_names:
            writer.u30(name)


def _write_instance(writer: Writer, instance: Instance) -> None:
    writer.u30(instance.name)
    writer.u30(instance.super_name)
    writer.u8(instance.flags)
    if instance.flags & CLASS_PROTECTED_NS:
        writer.u30(instance.protected_ns)
    _write_u30_list(writer, instance.interfaces)
    writer.u30(instance.constructor)
    _write_traits(writer, instance.traits)


def _write_traits(writer: Writer, traits: list[Trait]) -> None:
    writer.u30(len(traits))
    for trait in traits:
        writer.u30(trait.name)
        writer.u8(trait.kind)
        if trait.base_kind in (TRAIT_SLOT, TRAIT_CONST):
            writer.u30(trait.slot)
            writer.u30(trait.type)
            writer.u30(trait.value_index)
            if trait.value_index:
                writer.u8(trait.value_kind)
        elif trait.base_kind in (TRAIT_METHOD, TRAIT_GETTER, TRAIT_SETTER):
            writer.u30(trait.disp)
            writer.u30(trait.method)
        elif trait.base_kind == TRAIT_CLASS:
            writer.u30(trait.slot)
            writer.u30(trait.cls)
        elif trait.base_kind == TRAIT_FUNCTION:
            writer.u30(trait.slot)
            writer.u30(trait.method)
        if trait.kind & TRAIT_HAS_METADATA:
            _write_u30_list(writer, trait.metadata)


def _write_method_body(writer: Writer, body: MethodBody) -> None:
    for value in (body.method, body.max_stack, body.local_count, body.init_scope_depth, body.max_scope_depth):
        writer.u30(value)
    writer.u30(len(body.code))
    writer.raw(body.code)
    writer.u30(len(body.exceptions))
    for handler in body.exceptions:
        for value in (handler.start, handler.end, handler.target, handler.exception_type, handler.variable_name):
            writer.u30(value)
    _write_traits(writer, body.traits)
