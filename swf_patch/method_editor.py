"""Editing one method body: queue insertions and replacements, then commit them together.

Patterns are matched against the method's original code, and every edit refers to positions in
that original code, so edits don't disturb each other however many there are. `commit` builds the
new code, raises max_stack if needed, encodes it and verifies it.
"""
from __future__ import annotations

from .abcfile import ABC
from .errors import PatchError
from .instructions import Code, Instruction, decode, encode
from .patterns import MethodCode
from .verify import measure, verify

DEBUG_REGISTER_NAME = 1           # the `debug` instruction's first operand for naming a register


def debug_name(abc: ABC, register: int, name: str) -> Instruction:
    """The debug instruction naming a register (decompilers show `name` instead of `_locN_`)."""
    return Instruction("debug", DEBUG_REGISTER_NAME, abc.add_string(name), register - 1, 0)


class MethodEditor:
    def __init__(self, abc: ABC, method_index: int, where: str) -> None:
        self.abc, self.where = abc, where
        self.method = abc.methods[method_index]
        self.body = self.method.body
        if not self.body:
            raise PatchError(f"{where}: method has no body")
        self.code = MethodCode(decode(self.body.code))
        self._inserted: dict[int, Code] = {}      # item index -> code inserted before it
        self._replaced: dict[int, Code] = {}      # item index -> its replacement
        self._new_locals: list[tuple[int, str]] = []    # (register, name)

    # ----------------------------------------------------------------------- positions

    def start_point(self) -> int:
        """The item index right after the method's leading `getlocal0; pushscope`."""
        entries = self.code.entries
        if len(entries) < 2 or (entries[0].instruction.op, entries[1].instruction.op) != ("getlocal0", "pushscope"):
            raise PatchError(f"{self.where}: method doesn't start with getlocal0; pushscope")
        return entries[1].index + 1

    def returns(self) -> list[int]:
        """The item indices of the method's return instructions."""
        return [e.index for e in self.code.entries if e.instruction.op in ("returnvoid", "returnvalue")]

    # ----------------------------------------------------------------------- edits

    def new_local(self, name: str) -> int:
        """A new register, named in the debug information."""
        self.body.local_count += 1
        register = self.body.local_count - 1
        self._new_locals.append((register, name))
        return register

    def insert(self, index: int, code: Code) -> None:
        """Insert code before an item (several insertions at one point stay in call order)."""
        self._inserted.setdefault(index, []).extend(code)

    def replace(self, indices: list[int], code: Code) -> None:
        """Replace the items at these indices by code (labels between them are kept)."""
        if set(indices) & set(self._replaced):
            raise PatchError(f"{self.where}: instruction replaced twice")
        self._replaced[indices[0]] = list(code)
        for index in indices[1:]:
            self._replaced[index] = []

    def commit(self) -> None:
        if self.body.exceptions:
            # Its exception table addresses code by byte offset, which editing would shift.
            raise PatchError(f"{self.where}: the method has try/catch, and editing such methods isn't "
                             "supported yet (its exception handlers would point at the wrong code)")
        items = self.code.items
        new_code = self._debug_names()
        for index, item in enumerate(items):
            new_code += self._inserted.get(index, [])
            new_code += self._replaced.get(index, [item])
        new_code += self._inserted.get(len(items), [])
        try:
            peak_stack, _ = measure(self.abc, new_code)
            self.body.max_stack = max(self.body.max_stack, peak_stack)
            self.body.code = encode(new_code)
            verify(self.abc, new_code, self.body.max_stack, self.body.max_scope_depth - self.body.init_scope_depth)
        except ValueError as e:                   # VerifyError, or an operand encode() refuses
            raise PatchError(f"{self.where}: edited code doesn't verify: {e}")

    def _debug_names(self) -> Code:
        """Debug names for the new locals. Once a method has any debug names, decompilers show its
        unnamed parameters blank, so those get the names decompilers would use (paramN)."""
        if not self._new_locals:
            return []
        names = [debug_name(self.abc, register, name) for register, name in self._new_locals]
        named = {item.args[2] + 1 for item in self.code.items
                 if isinstance(item, Instruction) and item.op == "debug" and item.args[0] == DEBUG_REGISTER_NAME}
        for register in range(1, len(self.method.param_types) + 1):
            if register not in named:
                names.append(debug_name(self.abc, register, f"param{register}"))
        return names
