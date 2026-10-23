"""The patch set operations (patch-format.md "Operations"), one function each.

Each takes the shared context, the operation's body as parsed from YAML (validated by lint), and
a `where` prefix for error messages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import yaml

from . import patterns
from .abcfile import (ABC, CONSTANT_DOUBLE, CONSTANT_FALSE, CONSTANT_INT, CONSTANT_NULL, CONSTANT_TRUE, CONSTANT_UINT,
                      CONSTANT_UTF8, METHOD_HAS_OPTIONAL, QNAME, TRAIT_CONST, TRAIT_GETTER, TRAIT_METHOD, TRAIT_SETTER,
                      TRAIT_SLOT, MethodBody, MethodInfo, Trait)
from .assembler import Assembler, parse_number, push_value
from .errors import PatchError
from .instructions import Instruction, decode, encode
from .method_editor import MethodEditor, debug_name
from .names import Names
from .patchset import PatchSets, expand_code, make_labels_private, substitute
from .verify import VerifyError, measure


@dataclass
class Context:
    """What operations share while one ABC is patched."""
    abc: ABC
    sets: PatchSets
    values: dict[str, Any]                        # parameter values
    names: Names = field(init=False)
    captures: dict[str, Optional[int]] = field(default_factory=dict)    # from `capture` operations

    def __post_init__(self) -> None:
        self.names = Names(self.abc)


Body = dict[str, Any]


# --------------------------------------------------------------------------- set_initial_values

def set_initial_values(ctx: Context, body: Body, where: str) -> None:
    """Changes members' initial values, each checked against its expected value. A static member
    assigned in the class initializer gets that code changed (a number); otherwise the value stored
    with the member's declaration (`var x:int = 0`) is changed (a number, true, false, null or a
    string). Without `expect`, the member must have no initial value yet; without `value`, its
    initial value is removed. `set_constants` is another name for this operation."""
    abc, names = ctx.abc, ctx.names
    cls = names.find_class(body["class"])
    editor: Optional[MethodEditor] = None
    for member, spec in (body.get("values") or {}).items():
        trait, static = names.trait(cls, member)
        if static:
            editor = editor or MethodEditor(abc, abc.classes[cls].static_init, f"{where} {body['class']}.<static-init>")
            initializer = patterns.parse(f"findproperty {member}\n*\ninitproperty|setproperty {member}", where)
            matches = patterns.find(abc, editor.code, {"match": initializer})
            if matches or not trait.value_index and "expect" in spec:
                match, = patterns.select(matches, 1, where, f"initializer of {member}")
                _set_initializer_code(ctx, editor, match, member, spec, where)
                continue
        _set_stored_value(ctx, trait, member, spec, where)
    if editor:
        editor.commit()


def _set_initializer_code(ctx: Context, editor: MethodEditor, match: patterns.Match, member: str, spec: dict,
                          where: str) -> None:
    abc = ctx.abc
    index = match.parts["match"][1]                       # the instruction pushing the value
    instruction = editor.code.items[index]
    current = _pushed_number(abc, instruction)
    expected = parse_number(substitute(spec["expect"], ctx.values, where))
    if current != expected:
        shown = current if current is not None else instruction
        raise PatchError(f"{where}: {member} is {shown}, expected {expected}")
    value = parse_number(substitute(spec["value"], ctx.values, where))
    editor.replace([index], [push_value(abc, value)])


def _set_stored_value(ctx: Context, trait: Trait, member: str, spec: dict, where: str) -> None:
    abc = ctx.abc
    if "expect" in spec:
        if not trait.value_index:
            raise PatchError(f"{where}: {member} has no initial value stored with it (an instance field's may be "
                             "set by the constructor's code: edit that instead)")
        current = stored_value(abc, trait)
        expected = _number_word(_spec_value(spec["expect"], ctx, where), abc.name_of(trait.type) or "*")
        if not _same(current, expected):
            raise PatchError(f"{where}: {member} is {current!r}, expected {expected!r}")
    elif trait.value_index:
        raise PatchError(f"{where}: {member} is {stored_value(abc, trait)!r}, expected no initial value")
    if "value" in spec:
        type_name = abc.name_of(trait.type) or "*"
        trait.value_index, trait.value_kind = _initial_value(abc, _spec_value(spec["value"], ctx, where), type_name, where)
    else:
        trait.value_index, trait.value_kind = 0, None


def stored_value(abc: ABC, trait: Trait) -> Any:
    """The initial value stored with a member: a number, True, False, None (null) or a string."""
    readers = {CONSTANT_TRUE: lambda i: True, CONSTANT_FALSE: lambda i: False, CONSTANT_NULL: lambda i: None,
               CONSTANT_UTF8: abc.string, CONSTANT_INT: lambda i: abc.ints[i], CONSTANT_UINT: lambda i: abc.uints[i],
               CONSTANT_DOUBLE: lambda i: abc.doubles[i]}
    if trait.value_kind not in readers:
        raise PatchError(f"unsupported kind of initial value ({trait.value_kind})")
    return readers[trait.value_kind](trait.value_index)


def _spec_value(raw: Any, ctx: Context, where: str) -> Any:
    """An expect/value entry: a literal, or a {{parameter}} (read as YAML once substituted)."""
    if isinstance(raw, str) and "{{" in raw:
        return yaml.safe_load(substitute(raw, ctx.values, where))
    return raw


def _pushed_number(abc: ABC, instruction: Instruction) -> Optional[float]:
    if instruction.op in ("pushbyte", "pushshort"):
        return instruction.args[0]
    if instruction.op == "pushdouble":
        return abc.doubles[instruction.args[0]]
    return None


# --------------------------------------------------------------------------- add_field, add_method

def add_field(ctx: Context, body: Body, where: str) -> None:
    abc, names = ctx.abc, ctx.names
    cls = names.find_class(body["class"])
    static = bool(body.get("static", False))
    kind = TRAIT_CONST if body.get("const") else TRAIT_SLOT
    _refuse_existing_member(abc, cls, body["name"], f"{where}: {body['class']}", kind, static)
    traits = abc.classes[cls].traits if static else abc.instances[cls].traits
    namespace = names.namespace_for(cls, body["visibility"], static)
    name = abc.add_multiname((QNAME, namespace, abc.add_string(body["name"])))
    # Static fields live in the class object's slots and need a free slot id; instance fields
    # can leave it 0 (assigned by the VM).
    slot = max((t.slot for t in traits if t.base_kind in (TRAIT_SLOT, TRAIT_CONST)), default=0) + 1 if static else 0
    value_index, value_kind = _initial_value(abc, body["value"], body["type"], where) if "value" in body else (0, None)
    traits.append(Trait(name=name, kind=kind, slot=slot, type=_type(names, body["type"], where),
                        value_index=value_index, value_kind=value_kind))


def _type(names: Names, ref: Any, where: str) -> int:
    """Multiname index for a type written in a patch set: a class, package::Class, Vector.<T>, or *."""
    return 0 if str(ref) == "*" else names.type_name(str(ref))


NUMBER_WORDS = {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}


def _number_word(value: Any, type_name: str) -> Any:
    """NaN, Infinity and -Infinity, written as words (as in ActionScript), are numbers for a Number."""
    return NUMBER_WORDS[value] if type_name == "Number" and isinstance(value, str) and value in NUMBER_WORDS else value


def _same(a: Any, b: Any) -> bool:
    """Equal values (NaN equals NaN here; true doesn't equal 1)."""
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return a == b and (type(a) is bool) == (type(b) is bool)


def _initial_value(abc: ABC, value: Any, type_name: str, where: str) -> tuple[int, int]:
    """(value index, value kind) of a field's initial value or a parameter's default: true, false,
    null, a number (NaN, Infinity and -Infinity as words, for a Number) or a string. true, false
    and null have no pool entry; their index is conventionally their kind."""
    value = _number_word(value, type_name)
    if value is True or value is False or value is None:
        kind = {True: CONSTANT_TRUE, False: CONSTANT_FALSE, None: CONSTANT_NULL}[value]
        return kind, kind
    if isinstance(value, str):
        return abc.add_string(value), CONSTANT_UTF8
    if isinstance(value, int) and type_name == "uint" and 0 <= value < 2**32:
        return abc.add_uint(value), CONSTANT_UINT
    if isinstance(value, int) and -2**31 <= value < 2**31:
        return abc.add_int(value), CONSTANT_INT
    if isinstance(value, (int, float)):
        return abc.add_double(float(value)), CONSTANT_DOUBLE
    raise PatchError(f"{where}: value must be true, false, null, a number or a string")


METHOD_KINDS = {"method": TRAIT_METHOD, "get": TRAIT_GETTER, "set": TRAIT_SETTER}


def add_method(ctx: Context, body: Body, where: str) -> None:
    abc, names = ctx.abc, ctx.names
    cls = names.find_class(body["class"])
    static = bool(body.get("static", False))
    kind = METHOD_KINDS.get(body.get("kind", "method"))
    if kind is None:
        raise PatchError(f"{where}: kind must be method, get or set")
    _refuse_existing_member(abc, cls, body["name"], f"{where}: {body['class']}", kind, static)
    scope_depth = method_scope_depth(abc, cls, static, f"{where}: {body['class']}")
    params = [p if isinstance(p, dict) else {"type": p} for p in body.get("params") or []]
    returns = str(body.get("returns", "void" if kind == TRAIT_SETTER else "*"))
    if kind == TRAIT_GETTER and params or kind == TRAIT_SETTER and (len(params) != 1 or returns != "void"):
        raise PatchError(f"{where}: a getter takes no parameters; a setter takes one and returns void")
    defaults = [p for p in params if "default" in p]
    if defaults and any("default" not in p for p in params[len(params) - len(defaults):]):
        raise PatchError(f"{where}: only the last parameters can have defaults")

    # Registers: 0 is `this` (the class, for a static method), then the parameters, then the locals.
    local_names = list(body.get("locals") or [])
    registers = {p["name"]: i + 1 for i, p in enumerate(params) if p.get("name")}
    registers.update({name: len(params) + 1 + i for i, name in enumerate(local_names)})
    assembler = Assembler(abc, names, registers, ctx.captures, where, context=cls)
    text = expand_code(body.get("code"), ctx.sets.snippets, {}, ctx.values, where)
    code = [debug_name(abc, register, name) for name, register in registers.items()] + assembler.assemble(text)
    assembler.check_labels()
    try:
        peak_stack, peak_scope = measure(abc, code)
    except VerifyError as e:
        raise PatchError(f"{where}: new method doesn't verify: {e}")

    index = len(abc.methods)
    method_body = MethodBody(method=index, max_stack=peak_stack, local_count=1 + len(params) + len(local_names),
                             init_scope_depth=scope_depth, max_scope_depth=scope_depth + peak_scope,
                             code=encode(code))
    optional = [_initial_value(abc, p["default"], str(p["type"]), where) for p in defaults]
    abc.methods.append(MethodInfo(param_types=[_type(names, p["type"], where) for p in params],
                                  return_type=_type(names, returns, where), name=0,
                                  flags=METHOD_HAS_OPTIONAL if optional else 0,
                                  optional_values=optional or None, body=method_body))
    abc.body_order.append(index)
    namespace = names.namespace_for(cls, body.get("visibility", "public"), static)
    name = abc.add_multiname((QNAME, namespace, abc.add_string(body["name"])))
    (abc.classes[cls].traits if static else abc.instances[cls].traits).append(Trait(name=name, kind=kind, method=index))


def add_names(ctx: Context, body: Body, where: str) -> None:
    """Adds names the file doesn't contain yet, so later code can use them (code can only use names
    that exist: a misspelled one fails instead of creating a name nothing defines). Names already in
    the file are left as they are."""
    for token in body:
        try:
            ctx.names.add_name(str(token))
        except PatchError as e:
            raise PatchError(f"{where}: {e}")


def _refuse_existing_member(abc: ABC, cls: int, name: str, where: str, kind: int, static: bool) -> None:
    """Members are added once: an existing one means the file is already patched (or a clash). The
    one exception: the getter or setter completing a pair."""
    accessors = {TRAIT_GETTER, TRAIT_SETTER}
    for traits, is_static in ((abc.instances[cls].traits, False), (abc.classes[cls].traits, True)):
        for trait in traits:
            if abc.name_of(trait.name) == name and not (
                    {kind, trait.base_kind} == accessors and is_static == static):
                raise PatchError(f"{where} already has a member '{name}' (already patched?)")


def method_scope_depth(abc: ABC, cls: int, static: bool, where: str) -> int:
    """The scope depth a new method starts at: the one the class's methods of its kind share (for
    static methods, those of the class initializer if there are none)."""
    traits = abc.classes[cls].traits if static else abc.instances[cls].traits
    depths = {abc.methods[t.method].body.init_scope_depth for t in traits
              if t.method is not None and abc.methods[t.method].body}
    if static and not depths:
        depths = {abc.methods[abc.classes[cls].static_init].body.init_scope_depth}
    if len(depths) != 1:
        raise PatchError(f"{where}'s methods disagree on their scope depth {sorted(depths)}")
    return depths.pop()


# --------------------------------------------------------------------------- guard, capture

def guard(ctx: Context, body: Body, where: str) -> None:
    abc, names = ctx.abc, ctx.names
    skipped = names.class_method_indices(names.find_class(body["outside"])) if body.get("outside") else set()
    checks = [(mode, entry["pattern"], {"match": patterns.parse(entry["pattern"], where)})
              for mode in ("forbid", "require") for entry in body.get(mode) or []]
    found = [False] * len(checks)
    for index, method in enumerate(abc.methods):     # decode each method once, test every pattern on it
        if all(found):
            break
        if index in skipped or not method.body:
            continue
        code = patterns.MethodCode(decode(method.body.code))
        for i, (_, _, pattern) in enumerate(checks):
            if not found[i] and patterns.find(abc, code, pattern):
                found[i] = True
    for (mode, text, _), hit in zip(checks, found):
        if (mode == "forbid") == hit:
            raise PatchError(f"{where}: guard failed ({mode} '{text.strip()}'): {body.get('message', '')}")


def capture(ctx: Context, body: Body, where: str) -> None:
    abc = ctx.abc
    editor = MethodEditor(abc, ctx.names.method(body["method"]), where)
    matches = patterns.find(abc, editor.code, {"match": patterns.parse(body["pattern"], where)})
    if body.get("occurrence", "only") == "first":
        if not matches:
            raise PatchError(f"{where}: capture pattern not found in {body['method']}")
    else:
        patterns.select(matches, 1, where, "capture pattern")
    ctx.captures.update(matches[0].captures)


# --------------------------------------------------------------------------- edit

def edit(ctx: Context, body: Body, where: str) -> None:
    """Edit one method, or several identically (`methods:`, each with its own substitutions)."""
    targets = body["methods"] if "methods" in body else {body["method"]: {}}
    for method, substitutions in targets.items():
        _edit_method(ctx, method, substitutions or {}, body, f"{where} {method}")


def _edit_method(ctx: Context, method: str, substitutions: dict[str, Any], body: Body, where: str) -> None:
    method_index = ctx.names.method(method)
    editor = MethodEditor(ctx.abc, method_index, where)
    registers = {name: editor.new_local(name) for name in body.get("locals") or []}
    captures = dict(ctx.captures)                 # patterns in the steps may add their own
    assembler = Assembler(ctx.abc, ctx.names, registers, captures, where,
                          context=ctx.names.class_of_method(method_index))
    for k, step in enumerate(body.get("steps") or []):
        step_where = f"{where} step {k}"
        inserting = "insert" in step
        location = (step["insert"] if inserting else step["replace"]) or {}
        if inserting and "at" in location:
            matches = None
            points = _fixed_points(editor, location["at"], step_where)
        else:
            matches = _find_location(ctx, editor, location, substitutions, step_where)
            points = [_insertion_point(m) for m in matches] if inserting else []
            for m in matches:
                captures.update(m.captures)
        text = expand_code(step.get("code" if inserting else "with"), ctx.sets.snippets, substitutions,
                           ctx.values, step_where)
        if inserting:
            for point in points:
                editor.insert(point, assembler.assemble(_one_copy(text, len(points))))
        else:
            for m in matches:
                editor.replace(m.parts["match"], assembler.assemble(_one_copy(text, len(matches))))
    assembler.check_labels()
    editor.commit()


def _fixed_points(editor: MethodEditor, at: str, where: str) -> list[int]:
    if at == "method-start":
        return [editor.start_point()]
    if at != "before-each-return":
        raise PatchError(f"{where}: unknown 'at: {at}'")
    returns = editor.returns()
    if not returns:
        raise PatchError(f"{where}: method has no return")
    return returns


def _find_location(ctx: Context, editor: MethodEditor, location: Body, substitutions: dict[str, Any],
                   where: str) -> list[patterns.Match]:
    """The matches of a step's after/match/before patterns, as many as its `count` asks for."""
    parts = {part: patterns.parse(substitute(location[part], substitutions, where, strict=False), where)
             for part in patterns.PART_ORDER if part in location}
    return patterns.select(patterns.find(ctx.abc, editor.code, parts), location.get("count"), where, "pattern")


def _insertion_point(match: patterns.Match) -> int:
    """Where an insert goes: right after the `after:` part, else at the jump target's label (so
    jumps there run the new code), else right before the first instruction matched."""
    if match.parts.get("after"):
        return match.parts["after"][-1] + 1
    if match.jump_target_label is not None:
        return match.jump_target_label
    first_part = next(part for part in ("match", "before") if match.parts.get(part))
    return match.parts[first_part][0]


def _one_copy(text: str, copies: int) -> str:
    """Code applied at several points gets private labels in each copy."""
    return make_labels_private(text) if copies > 1 else text


OPERATIONS: dict[str, Callable[[Context, Body, str], None]] = {
    "set_initial_values": set_initial_values, "set_constants": set_initial_values,
    "add_names": add_names, "add_field": add_field, "add_method": add_method,
    "guard": guard, "capture": capture, "edit": edit,
}
