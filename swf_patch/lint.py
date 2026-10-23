"""Checking patch sets (patch-format.md) without applying them.

Valid YAML isn't enough: an indentation slip turns a label into a stray key and silently cuts a
code block short, and an unquoted comma in an inline map splits a string. This checks the
structure the format defines and, inside code, labels, locals, placeholders and snippet use.
"""
from __future__ import annotations

from typing import Any

import yaml

from .assembler import LABEL_LINE, code_lines
from .instructions import JUMP_OPS, REGISTER_OPS
from .patchset import PLACEHOLDER

TOP_LEVEL_KEYS = {"format", "target", "parameters", "profiles", "snippets", "patches"}
PATCH_KEYS = {"id", "title", "why", "as3", "when", "ops"}
OPERATION_KEYS = {
    "set_initial_values": {"class", "values"},
    "set_constants": {"class", "values"},             # set_initial_values' other name
    "add_names": set(),                               # a list, not a mapping: checked by _op_add_names
    "add_field": {"class", "name", "type", "visibility", "static", "const", "value"},
    "add_method": {"class", "name", "visibility", "static", "kind", "params", "returns", "locals", "code"},
    "guard": {"forbid", "require", "outside", "message"},
    "capture": {"method", "pattern", "occurrence"},
    "edit": {"method", "methods", "locals", "steps"},
}
VISIBILITIES = {"private", "protected", "internal", "public"}
NULLABLE_KEYS = {"add_field": {"value"}}         # keys where YAML null is a value (a field starting as null)
INSERT_AT = {"method-start", "before-each-return"}


def lint(paths: list[str]) -> list[str]:
    """Problems in patch sets that are applied together (they share parameters and snippets),
    as "path: where: problem" lines. Empty if all is well."""
    loaded = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            loaded.append((path, yaml.safe_load(f) or {}))
    parameters: dict[str, Any] = {}
    snippets: dict[str, Any] = {}
    for _, data in loaded:
        if isinstance(data, dict):
            parameters.update(data.get("parameters") or {})
            snippets.update(data.get("snippets") or {})
    problems = []
    for path, data in loaded:
        problems += [f"{path}: {p}" for p in Linter(parameters, snippets).lint(data)]
    return problems


class Linter:
    def __init__(self, parameters: dict[str, Any], snippets: dict[str, Any]) -> None:
        self.parameters = parameters              # from every patch set applied together
        self.snippets = snippets
        self.snippet_params = {p for s in snippets.values() if isinstance(s, dict) for p in s.get("params") or []}
        self.problems: list[str] = []

    def lint(self, data: Any) -> list[str]:
        if not self._keys("file", data, TOP_LEVEL_KEYS):
            return self.problems
        for name, spec in (data.get("parameters") or {}).items():
            if self._keys(f"parameters.{name}", spec, {"default", "doc"}) and "default" not in spec:
                self._problem(f"parameters.{name}", "no default")
        for profile, values in (data.get("profiles") or {}).items():
            for name in values or {}:
                if name not in self.parameters:
                    self._problem(f"profiles.{profile}", f"unknown parameter '{name}'")
        for name, snippet in (data.get("snippets") or {}).items():
            self._snippet(f"snippets.{name}", snippet)
        seen = set()
        for i, patch in enumerate(data.get("patches") or []):
            patch_id = patch.get("id", f"#{i}") if isinstance(patch, dict) else f"#{i}"
            if patch_id in seen:
                self._problem(f"patch {patch_id}", "duplicate id")
            seen.add(patch_id)
            self._patch(f"patch {patch_id}", patch)
        return self.problems

    # ----------------------------------------------------------------------- structure

    def _problem(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    def _keys(self, where: str, mapping: Any, allowed: set[str], nullable: frozenset[str] = frozenset()) -> bool:
        """Whether `mapping` is a mapping; reports keys it shouldn't have and keys without a value
        (unless null is a value there)."""
        if not isinstance(mapping, dict):
            self._problem(where, f"expected a mapping, got {type(mapping).__name__}")
            return False
        for key, value in mapping.items():
            if key not in allowed:
                hint = " (an escaped label? labels belong inside code blocks)" if value is None else ""
                self._problem(where, f"unexpected key '{key}'{hint}")
            elif value is None and key not in nullable:
                self._problem(where, f"key '{key}' has no value")
        return True

    def _snippet(self, where: str, snippet: Any) -> None:
        if not self._keys(where, snippet, {"params", "code"}):
            return
        code = snippet.get("code", "")
        self._jumps_have_labels(where, [code])
        self._placeholders(where, [code], set(self.parameters) | set(snippet.get("params") or []))

    def _patch(self, where: str, patch: Any) -> None:
        if not self._keys(where, patch, PATCH_KEYS):
            return
        for j, op in enumerate(patch.get("ops") or []):
            if not isinstance(op, dict) or len(op) != 1:
                self._problem(f"{where} op {j}", "each operation is a mapping with exactly one key")
                continue
            kind, body = next(iter(op.items()))
            op_where = f"{where} op {j} ({kind})"
            if kind not in OPERATION_KEYS:
                self._problem(op_where, "unknown operation")
            elif kind == "add_names":
                self._op_add_names(op_where, body)
            elif self._keys(op_where, body, OPERATION_KEYS[kind], frozenset(NULLABLE_KEYS.get(kind, ()))):
                check = getattr(self, f"_op_{kind}", None)
                if check:
                    check(op_where, body)

    # ----------------------------------------------------------------------- operations

    def _op_add_field(self, where: str, body: dict) -> None:
        if body.get("visibility") not in VISIBILITIES:
            self._problem(where, f"visibility must be one of {sorted(VISIBILITIES)}")

    def _op_capture(self, where: str, body: dict) -> None:
        self._pattern(where, body.get("pattern", ""))
        if body.get("occurrence", "only") not in ("only", "first"):
            self._problem(where, "occurrence must be 'only' or 'first'")

    def _op_guard(self, where: str, body: dict) -> None:
        for entry in (body.get("forbid") or []) + (body.get("require") or []):
            if self._keys(where, entry, {"pattern"}):
                self._pattern(where, entry.get("pattern", ""))

    def _op_set_initial_values(self, where: str, body: dict) -> None:
        for member, spec in (body.get("values") or {}).items():
            if not isinstance(spec, dict) or not spec or not set(spec) <= {"expect", "value"}:
                self._problem(where, f"{member}: write {{ expect: ..., value: ... }} (leave out expect if it has "
                                     "no initial value yet, value to remove it)")

    _op_set_constants = _op_set_initial_values

    def _op_add_names(self, where: str, body: Any) -> None:
        if not isinstance(body, list) or not body:
            self._problem(where, "expected a list of names")
            return
        for name in body:
            qualifier, separator, short = str(name).rpartition("::")
            if not separator or not qualifier or not short or "." in short:
                self._problem(where, f"'{name}': new names are written qualified: public::Name, package::Name, "
                                     "AS3::name or \"uri\"::name")

    def _op_add_method(self, where: str, body: dict) -> None:
        if body.get("visibility", "public") not in VISIBILITIES:
            self._problem(where, f"visibility must be one of {sorted(VISIBILITIES)}")
        if body.get("kind", "method") not in ("method", "get", "set"):
            self._problem(where, "kind must be method, get or set")
        params = body.get("params") or []
        if not isinstance(params, list):
            self._problem(where, "params is a list")
            params = []
        for param in params:
            if not (isinstance(param, str) or isinstance(param, dict) and "type" in param
                    and set(param) <= {"name", "type", "default"}):
                self._problem(where, "each parameter is a type, or { name, type, default }")
        texts = self._code_texts(where, body.get("code"))
        named = {p["name"] for p in params if isinstance(p, dict) and p.get("name")}
        self._registers(where, texts, set(body.get("locals") or []) | named)
        self._placeholders(where, texts, set(self.parameters))
        self._jumps_have_labels(where, texts)

    def _op_edit(self, where: str, body: dict) -> None:
        if ("method" in body) == ("methods" in body):
            self._problem(where, "needs exactly one of 'method' or 'methods'")
        substitutions: set[str] = set()
        if isinstance(body.get("methods"), dict):
            keysets = [set(v or {}) for v in body["methods"].values()]
            if any(keys != keysets[0] for keys in keysets):
                self._problem(where, "every method in 'methods' must define the same substitutions")
            substitutions = keysets[0] if keysets else set()
        local_names = set(body.get("locals") or [])
        own_texts = []                            # labels are shared by every step of the edit
        for k, step in enumerate(body.get("steps") or []):
            own_texts += self._edit_step(f"{where} step {k}", step, local_names, substitutions)
        self._jumps_have_labels(where, own_texts)

    def _edit_step(self, where: str, step: Any, local_names: set[str], substitutions: set[str]) -> list[str]:
        """Checks one step; returns its own code texts (without snippet bodies) for the label check."""
        if not isinstance(step, dict):
            self._problem(where, "step must be a mapping")
            return []
        if "insert" in step:
            action, code_key, location_keys = "insert", "code", {"at", "after", "before", "count"}
        elif "replace" in step:
            action, code_key, location_keys = "replace", "with", {"after", "match", "before", "count"}
        else:
            self._problem(where, "step needs 'insert' or 'replace'")
            return []
        self._keys(where, step, {action, code_key})
        location = step[action] or {}
        self._keys(where, location, location_keys)
        if action == "insert":
            if "at" in location:
                if location["at"] not in INSERT_AT:
                    self._problem(where, f"'at' must be one of {sorted(INSERT_AT)}")
                if "after" in location or "before" in location:
                    self._problem(where, "use either 'at' or after:/before:, not both")
            elif "after" not in location and "before" not in location:
                self._problem(where, "insert needs 'at', 'after' or 'before'")
        elif "match" not in location:
            self._problem(where, "replace needs 'match'")
        for part in ("after", "match", "before"):
            if part in location:
                self._pattern(f"{where} {part}", location[part])
        code = step.get(code_key, "")
        texts = self._code_texts(where, code)
        self._registers(where, texts, local_names)
        self._placeholders(where, texts, set(self.parameters) | substitutions | self.snippet_params)
        return self._code_texts(where, code, include_snippets=False, report=False)

    # ----------------------------------------------------------------------- code

    def _pattern(self, where: str, text: Any) -> None:
        if not str(text).strip():
            self._problem(where, "empty pattern")

    def _code_texts(self, where: str, code: Any, include_snippets: bool = True, report: bool = True) -> list[str]:
        """Code (text, or a list of text and snippet uses) as a list of texts, checking snippet uses.
        Without include_snippets, snippet bodies are left out (their labels are private)."""
        if code is None:
            return []
        if isinstance(code, str):
            return [code]
        if not isinstance(code, list):
            if report:
                self._problem(where, "code must be text or a list")
            return []
        texts = []
        for k, item in enumerate(code):
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and "snippet" in item:
                name = item["snippet"]
                if report:
                    self._keys(f"{where} code item {k}", item, {"snippet", "args"})
                if name not in self.snippets:
                    if report:
                        self._problem(where, f"unknown snippet '{name}'")
                    continue
                given, wanted = set(item.get("args") or {}), set(self.snippets[name].get("params") or [])
                if given != wanted and report:
                    self._problem(where, f"snippet '{name}' takes {sorted(wanted)}, got {sorted(given)}")
                if include_snippets:
                    texts.append(self.snippets[name].get("code", ""))
            elif report:
                self._problem(f"{where} code item {k}", "must be assembly text or { snippet: ..., args: ... }")
        return texts

    def _jumps_have_labels(self, where: str, texts: list[str]) -> None:
        defined, targets = set(), set()
        for text in texts:
            for line in code_lines(text):
                label = LABEL_LINE.match(line)
                op, _, operand = line.partition(" ")
                if label:
                    defined.add(label.group(1))
                elif op in JUMP_OPS and operand.strip():
                    targets.add(operand.strip())
        for target in sorted(targets - defined):
            self._problem(where, f"jump to undefined label '{target}'")

    def _registers(self, where: str, texts: list[str], local_names: set[str]) -> None:
        for text in texts:
            for line in code_lines(text):
                op, _, operand = line.partition(" ")
                operand = operand.strip()
                if op in REGISTER_OPS and operand and not operand.isdecimal() \
                        and not operand.startswith("{{") and operand not in local_names:
                    self._problem(where, f"'{op} {operand}': '{operand}' is not a declared local")

    def _placeholders(self, where: str, texts: list[str], allowed: set[str]) -> None:
        for text in texts:
            for name in PLACEHOLDER.findall(str(text)):
                if name not in allowed:
                    self._problem(where, f"undeclared placeholder '{{{{{name}}}}}'")
