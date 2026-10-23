"""Loading patch sets: YAML files, parameters and profiles, `when` conditions, `{{placeholder}}`
substitution, and turning code items (text and snippet uses) into assembly text.

Several patch sets can be applied in order (e.g. smoothmap.yaml then diagnostics.yaml). They share
one set of parameters, profiles and snippets, so names must not collide.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .assembler import LABEL_LINE, strip_comment
from .errors import PatchError
from .instructions import JUMP_OPS

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass
class Patch:
    """One entry of a patch set's `patches` list. Operations stay as parsed from YAML."""
    id: str
    ops: list[dict[str, Any]] = field(default_factory=list)
    when: Any = None                              # condition over parameters (see evaluate_when)
    title: str = ""
    why: str = ""
    as3: str = ""
    source: str = ""                              # the patch set file it came from


class PatchSets:
    """One or more patch set files, merged in order."""

    def __init__(self, paths: list[str]) -> None:
        self.parameters: dict[str, dict] = {}     # name -> {default, doc}
        self.profiles: dict[str, dict] = {}       # name -> {parameter: value}
        self.snippets: dict[str, dict] = {}       # name -> {params, code}
        self.patches: list[Patch] = []
        for path in paths:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                raise PatchError(f"{path}: not a patch set")
            for key, merged in (("parameters", self.parameters), ("profiles", self.profiles),
                                ("snippets", self.snippets)):
                for name, value in (data.get(key) or {}).items():
                    if name in merged:
                        raise PatchError(f"{path}: {key[:-1]} '{name}' is already defined by an earlier patch set")
                    merged[name] = value
            for entry in data.get("patches") or []:
                if any(p.id == entry.get("id") for p in self.patches):
                    raise PatchError(f"{path}: patch id '{entry.get('id')}' is already used")
                self.patches.append(Patch(id=entry["id"], ops=entry.get("ops") or [], when=entry.get("when"),
                                          title=entry.get("title", ""), why=entry.get("why", ""),
                                          as3=entry.get("as3", ""), source=path))

    def values(self, profile: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Parameter values: the defaults, then the profile's, then explicit overrides."""
        values = {name: spec["default"] for name, spec in self.parameters.items()}
        if profile:
            if profile not in self.profiles:
                raise PatchError(f"unknown profile '{profile}' (have: {', '.join(self.profiles) or 'none'})")
            values.update(self.profiles[profile] or {})
        for name, value in (overrides or {}).items():
            if name not in self.parameters:
                raise PatchError(f"unknown parameter '{name}'")
            values[name] = value
        return values

    def active(self, values: dict[str, Any]) -> list[Patch]:
        """The patches whose `when` condition holds."""
        return [p for p in self.patches if evaluate_when(p.when, values)]


# --------------------------------------------------------------------------- conditions

def evaluate_when(expr: Any, values: dict[str, Any]) -> bool:
    """A boolean expression over parameters: names, `and`, `or`, `not`, parentheses."""
    if expr is None:
        return True
    if isinstance(expr, bool):
        return expr
    tokens = re.findall(r"\(|\)|[A-Za-z_]\w*", str(expr))
    if "".join(tokens) != re.sub(r"\s+", "", str(expr)):
        raise PatchError(f"when: can't parse '{expr}'")
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str | None:
        nonlocal pos
        pos += 1
        return tokens[pos - 1] if pos <= len(tokens) else None

    def atom() -> bool:                           # not atom | ( disjunction ) | parameter
        token = take()
        if token == "not":
            return not atom()
        if token == "(":
            value = disjunction()
            if take() != ")":
                raise PatchError(f"when: missing ')' in '{expr}'")
            return value
        if token not in values:
            raise PatchError(f"when: unknown parameter '{token}'")
        return bool(values[token])

    def conjunction() -> bool:                    # atom (and atom)*
        value = atom()
        while peek() == "and":
            take()
            value = atom() and value
        return value

    def disjunction() -> bool:                    # conjunction (or conjunction)*
        value = conjunction()
        while peek() == "or":
            take()
            value = conjunction() or value
        return value

    result = disjunction()
    if pos != len(tokens):
        raise PatchError(f"when: unexpected '{tokens[pos]}' in '{expr}'")
    return result


# --------------------------------------------------------------------------- substitution

def format_value(value: Any) -> str:
    """A parameter value as text in assembly (booleans as true/false, floats exactly)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value) if isinstance(value, float) else str(value)


def substitute(text: Any, mapping: dict[str, Any], where: str, strict: bool = True) -> str:
    """Replace each {{name}} with its value. Unknown names fail, or stay as they are (strict=False)
    for a later substitution to fill."""
    def replacement(m: re.Match) -> str:
        name = m.group(1)
        if name in mapping:
            return format_value(mapping[name])
        if strict:
            raise PatchError(f"{where}: unknown placeholder '{{{{{name}}}}}'")
        return m.group(0)
    return PLACEHOLDER.sub(replacement, str(text))


_unique_suffixes = itertools.count(1)


def make_labels_private(text: str) -> str:
    """Rename the labels a piece of assembly defines, and the jumps to them, to names used nowhere
    else: for code that's inserted several times (a snippet used twice, a step applied at every
    return), so each copy has its own labels."""
    lines = text.splitlines()
    defined = {m.group(1) for m in map(LABEL_LINE.match, (line.strip() for line in lines)) if m}
    if not defined:
        return text
    suffix = f"__s{next(_unique_suffixes)}"
    out = []
    for line in lines:
        code = strip_comment(line).strip()
        label = LABEL_LINE.match(code)
        op, _, target = code.partition(" ")
        if label and label.group(1) in defined:
            line = f"{label.group(1)}{suffix}:"
        elif op in JUMP_OPS and target.strip() in defined:
            line = f"{op} {target.strip()}{suffix}"
        out.append(line)
    return "\n".join(out)


def expand_code(code: Any, snippets: dict[str, dict], substitutions: dict[str, Any], values: dict[str, Any],
                where: str) -> str:
    """Code (text, or a list of text and snippet uses) -> one assembly text, fully substituted.
    `substitutions`: an edit's per-method substitutions (`methods:`), applied before parameters."""
    if code is None:
        return ""
    texts = []
    for k, item in enumerate([code] if isinstance(code, str) else code):
        if isinstance(item, str):
            texts.append(substitute(substitute(item, substitutions, where, strict=False), values, where))
        elif isinstance(item, dict) and "snippet" in item:
            texts.append(_expand_snippet(item, snippets, substitutions, values, where))
        else:
            raise PatchError(f"{where}: code item {k} must be text or {{snippet: ..., args: ...}}")
    return "\n".join(texts)


def _expand_snippet(use: dict, snippets: dict[str, dict], substitutions: dict[str, Any], values: dict[str, Any],
                    where: str) -> str:
    name = use["snippet"]
    if name not in snippets:
        raise PatchError(f"{where}: unknown snippet '{name}'")
    snippet = snippets[name]
    args = use.get("args") or {}
    wanted = set(snippet.get("params") or [])
    if set(args) != wanted:
        raise PatchError(f"{where}: snippet '{name}' takes {sorted(wanted)}, got {sorted(args)}")
    resolved = {arg: substitute(substitute(value, substitutions, where, strict=False), values, where)
                for arg, value in args.items()}
    text = substitute(snippet.get("code", ""), resolved, where, strict=False)
    return make_labels_private(substitute(text, values, where))
