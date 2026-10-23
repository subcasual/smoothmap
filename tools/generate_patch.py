"""Write a patch set by editing ActionScript: export classes, edit them, build the patch set.

    python tools/generate_patch.py export vanilla HubMap [MapMenu ...]      # vanilla: REDkit's depot copy
    (edit build/edit/red/game/.../HubMap.as)
    python tools/generate_patch.py build -o patches/my-change.yaml
    python -m redswf_patch apply -p patches/my-change.yaml <depot copy, or another .redswf> out.redswf

The input can be a .redswf (its GFX payload is used; the patch set then applies to every copy of
the bytecode), a .swf or .gfx, or a mod folder or bundle (with --resource, default build.yaml's).
Needs JPEXS FFDec (and Java): found via --ffdec, the FFDEC environment variable, the default install
folders, or PATH (see ffdec_utils). See patch_generator/README.md for what can be generated so far.
"""
from __future__ import annotations

import argparse
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import patch_generator                       # noqa: E402
from patch_generator.workflow import MANIFEST  # noqa: E402
from redbundle import find_file              # noqa: E402
from redswf_patch import RedSwf              # noqa: E402
from vanilla import resolve                  # noqa: E402

DEFAULT_FOLDER = os.path.join(ROOT, "build", "edit")


def payload(source: str, resource: str | None) -> bytes:
    """The SWF/GFX to work on: the file itself, or a .redswf's GFX payload (from a mod if need be)."""
    if not resource:
        with open(os.path.join(ROOT, "build.yaml"), encoding="utf-8") as f:
            resource = yaml.safe_load(f)["resource"]
    try:
        data, _ = find_file(resolve(source), resource)
    except LookupError as e:
        raise SystemExit(str(e))
    return RedSwf(data).gfx if data[:4] == b"CR2W" else data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ffdec", help="FFDec's folder, ffdec-cli.exe or ffdec.jar")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="decompile classes into a folder to edit")
    export.add_argument("input")
    export.add_argument("classes", nargs="+", metavar="CLASS", help="Class or package::Class")
    export.add_argument("-o", "--folder", default=DEFAULT_FOLDER, help="default: build/edit")
    export.add_argument("--resource", help="depot path inside a mod (default: build.yaml's resource)")
    build = commands.add_parser("build", help="turn the edits into a patch set")
    build.add_argument("folder", nargs="?", default=DEFAULT_FOLDER, help="default: build/edit")
    build.add_argument("-o", "--out", help="the patch set to write (default: <folder>/patch.yaml)")
    build.add_argument("--input", help="the exported file, if it moved (default: as recorded at export)")
    build.add_argument("--resource", help="depot path inside a mod (default: build.yaml's resource)")
    args = parser.parse_args()

    try:
        ffdec = patch_generator.FFDec(args.ffdec)
        if args.command == "export":
            files = patch_generator.export(payload(args.input, args.resource), args.classes, args.folder, ffdec,
                                           source=os.path.abspath(resolve(args.input)))
            print("Edit these, then run: python tools/generate_patch.py build"
                  + (f" {args.folder}" if args.folder != DEFAULT_FOLDER else ""))
            for path in files:
                print(f"  {path}")
            return
        with open(os.path.join(args.folder, MANIFEST), encoding="utf-8") as f:
            source = args.input or yaml.safe_load(f)["source"]
        result = patch_generator.generate(payload(source, args.resource), args.folder, ffdec)
    except (patch_generator.GeneratorError, patch_generator.FFDecError) as e:
        raise SystemExit(f"error: {e}")
    out = args.out or os.path.join(args.folder, "patch.yaml")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(result.yaml)
    print(f"{out}: {', '.join(result.methods)}")
    print("Verified: it applies, and gives each method, member and initial value exactly what FFDec compiled "
          "from your edits.")
    print("Fill in each patch's `why`, then lint and apply it like any patch set.")


if __name__ == "__main__":
    main()
