"""ActionScript source text, as FFDec writes it and a modder edits it: file paths, finding a class's
methods in it, and preparing it for FFDec's compiler (NaN defaults, stubbing unedited methods)."""
from __future__ import annotations

import os
import re
import textwrap
from dataclasses import dataclass

# FFDec's compiler rejects NaN as a parameter default ("not a compile-time constant"). Defaults are
# part of a method's signature, which patches never change, so a placeholder compiles the same body.
NAN_DEFAULT = re.compile(r"(:\s*Number\s*=\s*)NaN\b")


def class_file(class_name: str) -> str:
    """Relative path of a class's source file: package.sub.Class -> package/sub/Class.as."""
    return os.path.join(*class_name.split(".")) + ".as"


def compilable(text: str) -> str:
    """The class's source as FFDec's compiler accepts it."""
    return NAN_DEFAULT.sub(r"\g<1>0", text)


DECLARATION = re.compile(r"^[^\n]*?\bfunction\s+(?:(get|set)\s+)?(\w+)\s*\(", re.MULTILINE)
SUPER_CALL = re.compile(r"\bsuper\s*\([^;]*\)\s*;")
DEFAULT_RETURNS = {"Boolean": "false", "int": "0", "uint": "0", "Number": "0"}


@dataclass
class MethodText:
    key: str                       # static, get/set and name: what tells a class's methods apart
    start: int                     # where its declaration's line starts
    body: int                      # its opening brace
    end: int                       # just after its closing brace
    returns: str | None            # declared return type


def methods(text: str) -> list[MethodText]:
    """The class's methods in its source, in order (functions nested in them aren't listed)."""
    found: list[MethodText] = []
    for declaration in DECLARATION.finditer(text):
        if found and declaration.start() < found[-1].end:
            continue                                  # inside the previous method's body
        body = text.find("{", declaration.end())
        end = _block_end(text, body) if body >= 0 else None
        if end is None:
            break
        prefix = text[declaration.start():declaration.start() + declaration.group(0).index("function")]
        returns = re.search(r":\s*([\w.$*<>]+)\s*$", text[declaration.end():body].rstrip())
        key = " ".join(filter(None, ["static" if re.search(r"\bstatic\b", prefix) else "",
                                     declaration.group(1), declaration.group(2)]))
        found.append(MethodText(key, declaration.start(), body, end, returns.group(1) if returns else None))
    return found


def stub_unedited(original: str, edited: str) -> tuple[str, str]:
    """Both versions of a class's source with every method body the edit left alone replaced by a
    stub, so FFDec only has to compile what was edited (its compiler fails on some code it
    decompiles, which matters only where it's kept). Stubs keep the line count, so FFDec's error
    line numbers still match the files, and constructors keep their super() call."""
    def bodies(text: str) -> dict[str, list[MethodText]]:
        by_key: dict[str, list[MethodText]] = {}
        for method in methods(text):
            by_key.setdefault(method.key, []).append(method)
        return by_key

    def same(a: str, b: str) -> bool:                 # editors change line endings and trailing spaces
        return [line.rstrip() for line in a.splitlines()] == [line.rstrip() for line in b.splitlines()]

    old, new = bodies(original), bodies(edited)
    unedited = {(key, n) for key in old.keys() & new.keys() if len(old[key]) == len(new[key])
                for n, (a, b) in enumerate(zip(old[key], new[key]))
                if same(original[a.start:a.end], edited[b.start:b.end])}

    def stubbed(text: str, by_key: dict[str, list[MethodText]]) -> str:
        spans = sorted(((m.body, m.end, m) for key, ms in by_key.items() for n, m in enumerate(ms)
                        if (key, n) in unedited), key=lambda span: span[0], reverse=True)
        for body, end, method in spans:
            text = text[:body] + _stub(text[body:end], method) + text[end:]
        return text

    return stubbed(original, old), stubbed(edited, new)


def with_register_probes(text: str) -> tuple[str, dict[str, list[str]]]:
    """The source with `trace(a, b, ...)` at the start of every method declaring variables, and
    {method key: [a, b, ...]}. FFDec's compile of it shows each variable's register (the trace call
    reads them in order): there is no simple rule for how FFDec numbers them. Each probe goes on the
    line of the method's opening brace, so line numbers don't change."""
    declared: dict[str, list[str]] = {}
    for method in reversed(methods(text)):
        names = list(dict.fromkeys(re.findall(r"\b(?:var|const)\s+(\w+)", text[method.body:method.end])))
        if names:
            declared[method.key] = names
            text = text[:method.body + 1] + f"trace({','.join(names)});" + text[method.body + 1:]
    return text, declared


def _stub(body: str, method: MethodText) -> str:
    super_call = SUPER_CALL.search(body)
    if super_call:                                    # constructors: the base class is still constructed
        statement = super_call.group(0).replace("\n", " ")
    elif method.returns in (None, "void", "*"):
        statement = ""
    else:
        statement = f"return {DEFAULT_RETURNS.get(method.returns, 'null')};"
    return "{" + statement + "\n" * body.count("\n") + "}"


def method_source(text: str, class_short_name: str, member: str) -> str | None:
    """The source of one method in a class's file (signature to closing brace, dedented), or None.
    member as tools name it: name, get name, set name, static name, <constructor>."""
    words = member.split()
    if words and words[0] == "static":
        words = words[1:]
    if words == ["<constructor>"]:
        words = [class_short_name]
    if not words or words[-1].startswith("<"):
        return None                                   # <static-init>: statements spread over the class
    accessor = rf"{words[0]}\s+" if len(words) == 2 else ""
    declaration = re.search(rf"^[^\n]*\bfunction\s+{accessor}{re.escape(words[-1])}\s*\(", text, re.MULTILINE)
    if not declaration:
        return None
    end = _block_end(text, text.index("{", declaration.end()))
    return textwrap.dedent(text[declaration.start():end]) if end else None


def _block_end(text: str, start: int) -> int | None:
    """The index after the brace closing the block that opens at `start`, skipping strings and
    comments."""
    depth, i = 0, start
    while i < len(text):
        char = text[i]
        if char in "\"'":
            i += 1
            while i < len(text) and text[i] != char:
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            if i < 0:
                return None
        elif text.startswith("/*", i):
            i = text.find("*/", i) + 1
            if i <= 0:
                return None
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None
