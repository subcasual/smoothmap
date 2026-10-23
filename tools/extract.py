"""Extract a game file from a mod: from its bundles, or a loose copy.

    python tools/extract.py mods/modmapredraw                  # -> build/panel_worldmap.redswf
    python tools/extract.py mods/modmapredraw/content/blob0.bundle -o other.redswf
    python tools/extract.py <mod> --resource gameplay\\gui_new\\swf\\hud\\hud_minimap2.redswf
    python tools/extract.py vanilla --swf          # the Flash file inside REDkit's depot copy, for FFDec

The default resource is build.yaml's. The output can be inspected (disasm.py, diff.py, as3diff.py)
and patched like any .redswf (published mods contain cooked files: GFX payload only). With --swf,
the output is the Flash file inside instead: the source SWF of a depot file, or the GFX payload of a
cooked one (Scaleform's format; FFDec opens both).
"""
from __future__ import annotations

import argparse
import ntpath
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from redbundle import find_file               # noqa: E402
from redswf_patch import RedSwf               # noqa: E402
from vanilla import resolve                   # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="vanilla (REDkit's depot copy), a mod folder, one of its .bundle files, or a .redswf")
    parser.add_argument("--resource", help="depot path of the file (default: build.yaml's resource)")
    parser.add_argument("-o", "--out", help="output file (default: build/<file name>)")
    parser.add_argument("--swf", action="store_true", help="write the Flash file inside (for FFDec) instead")
    args = parser.parse_args()
    resource = args.resource
    if not resource:
        with open(os.path.join(ROOT, "build.yaml"), encoding="utf-8") as f:
            resource = yaml.safe_load(f)["resource"]
    try:
        data, where = find_file(resolve(args.source), resource)
    except LookupError as e:
        raise SystemExit(str(e))
    name = ntpath.basename(resource)
    if args.swf:
        resource_file = RedSwf(data)
        data = resource_file.swf if resource_file.swf is not None else resource_file.gfx
        name = os.path.splitext(name)[0] + (".gfx" if resource_file.swf is None else ".swf")
    out = args.out or os.path.join(ROOT, "build", name)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)
    print(f"{where}\n  -> {out} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
