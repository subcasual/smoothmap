"""The patches the generator writes, before they're written out as YAML (writing.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

NO_VALUE = object()                # a member or parameter without an initial value or default
NULL = object()                    # one whose initial value or default is null


@dataclass
class Step:                        # one step of an anchored edit (see align.py)
    after: list[str]               # pattern lines: original instructions before the change
    match: list[str]               # pattern lines: the original instructions replaced (none: an insert)
    before: list[str]              # pattern lines: original instructions after the change
    code: list[str]                # assembly lines: the new instructions


@dataclass
class FieldPatch:                  # add_field
    ref: str                       # Class.name
    type: str                      # as add_field writes it: Class, package::Class, Vector.<T> or *
    visibility: str
    static: bool
    value: Any = NO_VALUE
    as3: str | None = None
    const: bool = False


@dataclass
class NewMethodPatch:              # add_method
    ref: str                       # Class.member, as tools name it (Class.get x, Class.static y)
    visibility: str
    returns: str                   # as add_method writes it: Class, package::Class, void or *
    code: list[str]
    locals: list[str] = field(default_factory=list)
    as3: str | None = None
    registers: dict[int, int] = field(default_factory=dict)   # FFDec's register -> the added method's
    new_names: list[str] = field(default_factory=list)        # names its code needs the file to get (add_names)
    name: str = ""
    static: bool = False
    kind: str = "method"           # method, get or set
    params: list[tuple[str | None, str, Any]] = field(default_factory=list)   # (name, type, default or NO_VALUE)


@dataclass
class ValuesPatch:                 # set_initial_values
    ref: str                       # the class, as patch sets write it
    values: list[tuple[str, Any, Any]]   # (member, original value, new value), either may be NO_VALUE
    as3: str | None = None
    in_code: set[str] = field(default_factory=set)   # members set by the class initializer's code


@dataclass
class MethodPatch:                 # an edit changing only the edited instructions (see align)
    ref: str                       # the method as patch sets write it: Class.member
    steps: list[Step]
    expected: list[str]            # the patched method's code, for verification
    locals: list[str] = field(default_factory=list)
    as3: str | None = None
    new_names: list[str] = field(default_factory=list)


@dataclass
class WholeMethodPatch:            # an edit replacing the method's whole code
    ref: str
    original: list[str]            # pattern lines: the method's original code
    code: list[str]                # assembly lines: its new code, as FFDec compiled it
    locals: list[str] = field(default_factory=list)
    as3: str | None = None
    registers: dict[int, int] = field(default_factory=dict)   # FFDec's register -> the patched method's
    new_names: list[str] = field(default_factory=list)


Patch = Union[FieldPatch, NewMethodPatch, ValuesPatch, MethodPatch, WholeMethodPatch]
# New members first, so edits can use them; then initial values; then the edits.
ORDER = {FieldPatch: 0, NewMethodPatch: 1, ValuesPatch: 2, MethodPatch: 3, WholeMethodPatch: 3}
