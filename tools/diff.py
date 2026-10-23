"""Show exactly what differs between two .redswf files, at the bytecode level.

    python tools/diff.py vanilla build/panel_worldmap.redswf      # vanilla: REDkit's depot copy
    python tools/diff.py A.redswf B.redswf --debug      keep debug/debugline/debugfile
    python tools/diff.py A.redswf B.redswf --context 3  lines of context (default 5)

Reports container-level changes (payload sizes, textures, resource properties), constant pool
entries added, fields added/removed/retyped, and a unified diff of every method whose code
changed, plus the methods only one file has. Methods are matched by their qualified names, so
files recompiled by another tool (all methods renumbered) compare correctly. Compares the GFX
payloads (what the game runs) and checks that each file's source SWF carries the same bytecode.
"""
from __future__ import annotations

import argparse
import difflib
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from redswf_patch.redswf import CHUNK_OFFSET, CHUNK_SIZE, RedSwf    # noqa: E402
from vanilla import resolve                                         # noqa: E402
from swf_patch.abcfile import ABC, MethodInfo                       # noqa: E402
from swf_patch.listing import disassemble, field_types, method_names   # noqa: E402
from swf_patch.names import Names                                   # noqa: E402
from swf_patch.swf import read_abc                                  # noqa: E402


def container_report(a: RedSwf, b: RedSwf) -> None:
    print("== Container")

    def size(payload: bytes | None) -> str:
        return "   (none)" if payload is None else f"{len(payload):>9,}"

    for label, x, y in (("GFX payload", a.gfx, b.gfx), ("source SWF", a.swf, b.swf)):
        print(f"  {label:12} {size(x)} -> {size(y)} bytes" + ("  (identical)" if x == y else ""))

    def chunk_data(r: RedSwf, chunk: list[int]) -> bytes:
        return bytes(r.data[chunk[CHUNK_OFFSET]:chunk[CHUNK_OFFSET] + chunk[CHUNK_SIZE]])

    textures_same = len(a.chunks) == len(b.chunks) and all(
        chunk_data(a, ca) == chunk_data(b, cb) for ca, cb in zip(a.chunks[1:], b.chunks[1:]))
    print(f"  textures     {len(a.chunks) - 1} chunks, " + ("identical" if textures_same else "DIFFERENT"))
    properties_same = a.data[a.swf_chunk_start:a.buffers_start] == b.data[b.swf_chunk_start:b.buffers_start]
    print("  properties   " + ("identical" if properties_same else "DIFFERENT (importFile, linkageName, header...)"))

    def bytecode_match(r: RedSwf) -> str:
        return "n/a (cooked)" if r.cooked else str(read_abc(r.swf) == read_abc(r.gfx))

    print(f"  source SWF bytecode matches GFX: A={bytecode_match(a)}, B={bytecode_match(b)}")


def pools_report(a: ABC, b: ABC) -> None:
    """Entries B's pools add after A's (patches append; anything else is reported as changed)."""
    print("\n== Constant pools (entries added)")
    for pool in ("ints", "doubles", "strings", "multinames"):
        old, new = getattr(a, pool), getattr(b, pool)
        if pool == "doubles":                       # compare bit patterns: NaN != NaN as numbers
            def bits(values: list) -> list:
                return [struct.pack("<d", v) if v is not None else None for v in values]
            same_prefix = bits(new[:len(old)]) == bits(old)
        else:
            same_prefix = new[:len(old)] == old
        if not same_prefix:
            print(f"  {pool}: existing entries CHANGED")
            continue
        added = new[len(old):]
        if pool == "strings":
            shown = [s.decode("utf8") for s in added]
        elif pool == "multinames":
            shown = [b.name_of(i) for i in range(len(old), len(new))]
        else:
            shown = added
        if added:
            print(f"  {pool}: +{len(added)} {shown}")


def fields_report(a: ABC, b: ABC) -> None:
    old, new = field_types(a), field_types(b)
    print("\n== Fields")
    for name in sorted(set(old) | set(new)):
        if name not in old:
            print(f"  + {name}: {new[name]}")
        elif name not in new:
            print(f"  - {name}: {old[name]}")
        elif old[name] != new[name]:
            print(f"  ~ {name}: {old[name]} -> {new[name]}")
    if len(a.instances) != len(b.instances):
        print(f"  classes: {len(a.instances)} -> {len(b.instances)}")


def methods_report(a: ABC, b: ABC, keep_debug: bool, context: int) -> None:
    old = {name: index for index, name in method_names(a).items()}
    new = {name: index for index, name in method_names(b).items()}

    def body_of(method: MethodInfo) -> tuple | None:
        return (method.body.code, method.body.local_count) if method.body else None

    changed = [name for name in new if name in old and body_of(a.methods[old[name]]) != body_of(b.methods[new[name]])]
    added = [name for name in new if name not in old]
    removed = [name for name in old if name not in new]
    print(f"\n== Methods changed: {len(changed)}" + (f", added: {len(added)}" if added else "")
          + (f", removed: {len(removed)}" if removed else ""))
    for sign, names in (("~", changed), ("+", added), ("-", removed)):
        for name in names:
            print(f"  {sign} {name}")
    names_a, names_b = Names(a), Names(b)
    for name in changed + added + removed:
        before = disassemble(a, old[name], keep_debug, names_a) if name in old else []
        after = disassemble(b, new[name], keep_debug, names_b) if name in new else []
        diff = difflib.unified_diff(before, after, f"A: {name}", f"B: {name}", n=context, lineterm="")
        print("\n" + "\n".join(diff))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--debug", action="store_true", help="keep debug instructions")
    parser.add_argument("--context", type=int, default=5, help="lines of context (default 5)")
    args = parser.parse_args()

    resources = []
    for path in (args.a, args.b):
        with open(resolve(path), "rb") as f:
            resources.append(RedSwf(f.read()))
    container_report(*resources)
    a, b = (ABC(read_abc(r.gfx)) for r in resources)
    pools_report(a, b)
    fields_report(a, b)
    methods_report(a, b, args.debug, args.context)


if __name__ == "__main__":
    main()
