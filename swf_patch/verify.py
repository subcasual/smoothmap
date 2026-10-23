"""Static checks of a method body: stack and scope depths along every path.

The AVM2 rejects a method whose operand stack could underflow, whose stack depth differs between
two paths meeting at the same instruction, or which exceeds the max_stack / max_scope_depth its
body declares. `measure` computes the depths a body needs; `verify` checks them against limits.
Exception handlers aren't followed (the patches here don't add any).
"""
from __future__ import annotations

from .abcfile import ABC, MULTINAME_L, MULTINAME_LA, RTQNAME, RTQNAME_A, RTQNAME_L, RTQNAME_LA
from .instructions import JUMP_OPS, Code, Instruction, Label, labels_in


class VerifyError(ValueError):
    """The code would be rejected by the AVM2 verifier."""


def _same(names: str, pops: int, pushes: int) -> dict[str, tuple[int, int]]:
    return {name: (pops, pushes) for name in names.split()}


# (values popped, values pushed) for instructions whose effect doesn't depend on their operands
FIXED_EFFECTS = {
    **_same("nop label bkpt bkptline timestamp debug debugline debugfile dxns kill "
            "inclocal declocal inclocal_i declocal_i returnvoid jump popscope", 0, 0),
    **_same("pushnull pushundefined pushtrue pushfalse pushnan pushbyte pushshort pushint pushuint "
            "pushdouble pushstring pushnamespace getlocal getlocal0 getlocal1 getlocal2 getlocal3 "
            "getglobalscope getscopeobject getglobalslot newactivation newcatch newfunction finddef "
            "hasnext2", 0, 1),
    **_same("pop setlocal setlocal0 setlocal1 setlocal2 setlocal3 returnvalue throw iftrue iffalse "
            "lookupswitch pushscope pushwith setglobalslot dxnslate", 1, 0),
    **_same("not negate negate_i increment increment_i decrement decrement_i bitnot typeof "
            "convert_i convert_u convert_d convert_b convert_s convert_o checkfilter esc_xelem esc_xattr "
            "coerce coerce_a coerce_b coerce_d coerce_i coerce_o coerce_s coerce_u astype istype "
            "getslot newclass li8 li16 li32 lf32 lf64 sxi1 sxi8 sxi16", 1, 1),
    **_same("add add_i subtract subtract_i multiply multiply_i divide modulo lshift rshift urshift "
            "bitand bitor bitxor equals strictequals lessthan lessequals greaterthan greaterequals "
            "instanceof in astypelate istypelate hasnext nextname nextvalue", 2, 1),
    **_same(" ".join(JUMP_OPS - {"jump", "iftrue", "iffalse"}), 2, 0),     # the comparing jumps
    **_same("setslot si8 si16 si32 sf32 sf64", 2, 0),
    "dup": (1, 2),
    "swap": (2, 2),
}

SCOPE_CHANGES = {"pushscope": 1, "pushwith": 1, "popscope": -1}
ENDS_PATH = {"returnvoid", "returnvalue", "throw", "jump", "lookupswitch"}

# Multinames whose namespace and/or name are popped from the stack at run time
RUNTIME_NAME_VALUES = {RTQNAME: 1, RTQNAME_A: 1, RTQNAME_L: 2, RTQNAME_LA: 2, MULTINAME_L: 1, MULTINAME_LA: 1}


def stack_effect(abc: ABC, instruction: Instruction) -> tuple[int, int]:
    """(values popped, values pushed) by one instruction."""
    op, args = instruction.op, instruction.args
    if op in FIXED_EFFECTS:
        return FIXED_EFFECTS[op]

    def runtime_name() -> int:
        """Values a multiname operand takes from the stack."""
        kind = abc.multinames[args[0]][0]
        return RUNTIME_NAME_VALUES.get(kind, 0)

    if op in ("getlex", "findpropstrict", "findproperty"):
        return runtime_name(), 1
    if op in ("getproperty", "getsuper", "getdescendants", "deleteproperty"):
        return 1 + runtime_name(), 1                        # object (+ name parts)
    if op in ("setproperty", "initproperty", "setsuper"):
        return 2 + runtime_name(), 0                        # object, value
    if op in ("callproperty", "callproplex", "callsuper", "constructprop"):
        return 1 + args[1] + runtime_name(), 1              # object, arguments
    if op in ("callpropvoid", "callsupervoid"):
        return 1 + args[1] + runtime_name(), 0
    if op in ("callmethod", "callstatic"):
        return 1 + args[1], 1
    if op == "constructsuper":
        return 1 + args[0], 0
    if op in ("construct", "applytype"):
        return 1 + args[0], 1                               # function/class, arguments
    if op == "call":
        return 2 + args[0], 1                               # function, receiver, arguments
    if op == "newobject":
        return 2 * args[0], 1                               # name/value pairs
    if op == "newarray":
        return args[0], 1
    raise VerifyError(f"no stack effect known for '{op}'")


def measure(abc: ABC, code: Code) -> tuple[int, int]:
    """(peak stack depth, peak scope depth) the code needs."""
    return _walk(abc, code)


def verify(abc: ABC, code: Code, max_stack: int, max_scope: int) -> None:
    """Raise VerifyError if the code is inconsistent or exceeds the limits (max_scope: the scope
    depth the method may add, i.e. max_scope_depth - init_scope_depth)."""
    _walk(abc, code, max_stack, max_scope)


def _walk(abc: ABC, code: Code, max_stack: int | None = None, max_scope: int | None = None) -> tuple[int, int]:
    """Follow every path from the first instruction, tracking stack and scope depths."""
    label_position = {item: i for i, item in enumerate(code) if isinstance(item, Label)}
    depths_at: dict[int, tuple[int, int]] = {}             # instruction position -> (stack, scope)
    pending = [(0, 0, 0)]                                  # paths to follow: (position, stack, scope)
    peak_stack = peak_scope = 0
    while pending:
        position, stack, scope = pending.pop()
        while position < len(code):
            item = code[position]
            if isinstance(item, Label):
                position += 1
                continue
            if position in depths_at:                      # joined a path already followed
                if depths_at[position] != (stack, scope):
                    raise VerifyError(f"inconsistent depth at #{position} {item}: "
                                      f"{depths_at[position]} vs {(stack, scope)}")
                break
            depths_at[position] = (stack, scope)
            pops, pushes = stack_effect(abc, item)
            if stack < pops:
                raise VerifyError(f"stack underflow at #{position} {item}")
            stack += pushes - pops
            scope += SCOPE_CHANGES.get(item.op, 0)
            peak_stack, peak_scope = max(peak_stack, stack), max(peak_scope, scope)
            if max_stack is not None and peak_stack > max_stack:
                raise VerifyError(f"stack {peak_stack} > max_stack {max_stack} at #{position} {item}")
            for target in labels_in(item.args):
                pending.append((label_position[target], stack, scope))
            if item.op in ENDS_PATH:
                break
            position += 1
    if max_scope is not None and peak_scope > max_scope:
        raise VerifyError(f"scope depth {peak_scope} > {max_scope}")
    return peak_stack, peak_scope
