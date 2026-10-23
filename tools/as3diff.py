"""ActionScript-level diff of two .redswf files, using JPEXS FFDec to decompile.

    python tools/as3diff.py vanilla build/panel_worldmap.redswf      # vanilla: REDkit's depot copy
    python tools/as3diff.py A.redswf B.redswf --out as3.diff --keep as3_export

Only the classes whose bytecode differs are decompiled (found with our own bytecode
comparison), so this takes seconds rather than decompiling all ~5000 classes.

FFDec is found by ffdec_utils: --ffdec, the FFDEC environment variable, the default Windows
install folders, then PATH. FFDec's library (ffdec_lib.jar, run with Java 8+) is preferred where
found; else a full install's command line. --ffdec can name either (see ffdec_utils).
Decompiled output is for reading only: FFDec's recompiler is what broke this file in the
past, but its decompiler is fine for viewing. tools/diff.py is the exact record.
"""
from __future__ import annotations

import argparse
import difflib
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ffdec_utils import FFDec, FFDecError             # noqa: E402
from redswf_patch import RedSwf                        # noqa: E402
from swf_patch.abcfile import ABC                      # noqa: E402
from swf_patch.listing import class_name               # noqa: E402
from swf_patch.swf import read_abc                     # noqa: E402
from vanilla import resolve                            # noqa: E402


def class_signature(abc: ABC, class_index: int) -> tuple:
    """What decides whether a class's decompiled source differs: its members' names and kinds and
    its methods' code. (Members' types and initial values are pool indices, which can differ
    between files without any visible change, so they're left out.)"""
    instance, cls = abc.instances[class_index], abc.classes[class_index]

    def members(traits: list) -> list[tuple]:
        return [(abc.name_of(t.name), t.base_kind) for t in traits]

    methods = [instance.constructor, cls.static_init] + [t.method for t in instance.traits + cls.traits
                                                          if t.method is not None]
    bodies = [abc.methods[m].body.code if abc.methods[m].body else None for m in methods]
    return members(instance.traits), members(cls.traits), bodies


def changed_classes(abc_a: ABC, abc_b: ABC) -> list[str]:
    """B's classes that A lacks or that differ, by qualified name (package.Class, as FFDec takes them)."""
    a_by_name = {class_name(abc_a, i, "."): i for i in range(len(abc_a.instances))}
    changed = []
    for i in range(len(abc_b.instances)):
        name = class_name(abc_b, i, ".")
        if name not in a_by_name or class_signature(abc_a, a_by_name[name]) != class_signature(abc_b, i):
            changed.append(name)
    return changed

def extract_swf(resource: RedSwf, path: str) -> str:
    """Write the embedded SWF for FFDec. Prefer the plain source SWF; fall back to the GFX
    payload if the two ever carry different bytecode (the game runs the GFX)."""
    if resource.swf is not None and read_abc(resource.swf) == read_abc(resource.gfx):
        data, path = resource.swf, path + ".swf"
    else:
        if resource.swf is not None:
            print("note: source SWF and GFX bytecode differ; decompiling the GFX (what the game runs)")
        data, path = resource.gfx, path + ".gfx"     # cooked files only have the GFX
    with open(path, "wb") as f:
        f.write(data)
    return path


def as_files(root: str) -> dict[str, str]:
    found = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            if name.endswith(".as"):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                rel = rel.split("scripts/", 1)[-1]                  # drop FFDec's "scripts/" prefix
                found[rel] = full
    return found


def read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n").splitlines()


def as3_diff(data_a: bytes, data_b: bytes, ffdec: str | None = None, context: int = 4, keep: str | None = None,
             log: Callable[[str], Any] = print) -> str:
    """Unified diff of the decompiled classes that differ between two .redswf files (bytes).
    Deterministic: files in sorted order, relative paths, no timestamps. Raises SystemExit if
    FFDec or Java is missing or FFDec fails."""
    ra, rb = RedSwf(data_a), RedSwf(data_b)
    classes = changed_classes(ABC(read_abc(ra.gfx)), ABC(read_abc(rb.gfx)))
    if not classes:
        return "No class differs."
    log("Changed classes:\n  " + "\n  ".join(classes))
    try:
        decompiler = FFDec(ffdec, library=True)
    except FFDecError as e:
        raise SystemExit(str(e))
    work = keep or tempfile.mkdtemp(prefix="as3diff_")
    try:
        dirs = {}
        for tag, res in (("a", ra), ("b", rb)):
            os.makedirs(os.path.join(work, tag), exist_ok=True)
            swf = extract_swf(res, os.path.join(work, f"{tag}_input"))
            log(f"Decompiling {tag.upper()} with FFDec...")
            try:
                decompiler.decompile(swf, classes, os.path.join(work, tag))
            except FFDecError as e:
                raise SystemExit(str(e))
            dirs[tag] = as_files(os.path.join(work, tag))
        output = []
        for rel in sorted(set(dirs["a"]) | set(dirs["b"])):
            a_lines = read_lines(dirs["a"][rel]) if rel in dirs["a"] else []
            b_lines = read_lines(dirs["b"][rel]) if rel in dirs["b"] else []
            output += difflib.unified_diff(a_lines, b_lines, f"A/{rel}", f"B/{rel}", n=context, lineterm="")
        return "\n".join(output) if output else "Decompiled sources are identical (differences are below what FFDec shows)."
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--ffdec", help="FFDec's folder, its library's (ffdec_lib.jar), or ffdec-cli.exe / ffdec.bat / ffdec.jar")
    ap.add_argument("--out", help="write the diff to this file instead of the console")
    ap.add_argument("--keep", metavar="DIR", help="keep the decompiled sources in DIR/a and DIR/b")
    ap.add_argument("--context", type=int, default=4)
    ap.add_argument("--list", action="store_true", help="only list the changed classes")
    args = ap.parse_args()

    with open(resolve(args.a), "rb") as f:
        data_a = f.read()
    with open(resolve(args.b), "rb") as f:
        data_b = f.read()
    if args.list:
        classes = changed_classes(ABC(read_abc(RedSwf(data_a).gfx)), ABC(read_abc(RedSwf(data_b).gfx)))
        print("Changed classes:\n  " + "\n  ".join(classes) if classes else "No class differs.")
        return
    text = as3_diff(data_a, data_b, args.ffdec, args.context, args.keep)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
        print(f"Diff written to {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
