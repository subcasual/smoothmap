"""Inspect the ActionScript bytecode inside a .redswf.

    python tools/disasm.py vanilla --classes [filter]           list classes
    python tools/disasm.py vanilla HubMap                       list a class's members
    python tools/disasm.py vanilla HubMap scrollMap             disassemble a method
    python tools/disasm.py vanilla HubMap "<constructor>"       (also: "<static-init>")
    python tools/disasm.py <file> HubMap scrollMap --debug      keep debug/debugline/debugfile

`vanilla` is REDkit's depot copy of the file (build.yaml's redkit.depot); any .redswf, SWF or GFX works.

Classes are written as in patch sets: the short name if unique, else package::Class. The output
uses the patch sets' assembly syntax, so patterns can be copied from it. Reads the GFX payload
(what the game runs); --swf reads the embedded source SWF instead.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from redswf_patch import PatchError, load_abc                             # noqa: E402
from vanilla import resolve                                               # noqa: E402
from swf_patch.listing import TRAIT_KIND_WORDS, class_name, disassemble   # noqa: E402
from swf_patch.names import Names                                         # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file")
    parser.add_argument("cls", nargs="?", help="class: Name or package::Name")
    parser.add_argument("member", nargs="?", help="method, <constructor> or <static-init>")
    parser.add_argument("--classes", nargs="?", const="", metavar="FILTER", help="list classes (containing FILTER)")
    parser.add_argument("--debug", action="store_true", help="keep debug instructions")
    parser.add_argument("--swf", action="store_true", help="read the source SWF instead of the GFX payload")
    args = parser.parse_args()
    abc = load_abc(resolve(args.file), args.swf)

    if args.classes is not None or not args.cls:
        for i in range(len(abc.instances)):
            name = class_name(abc, i)
            if (args.classes or "").lower() in name.lower():
                print(name)
        return

    names = Names(abc)
    try:
        cls = names.find_class(args.cls)
        if args.member:
            member = "<static-init>" if args.member == "<static init>" else args.member     # old spelling
            print("\n".join(disassemble(abc, names.method(f"{args.cls}.{member}"), args.debug, names)))
            return
    except PatchError as e:
        raise SystemExit(str(e))
    instance = abc.instances[cls]
    print(f"class {class_name(abc, cls)} extends {abc.name_of(instance.super_name)}")
    for side, traits in (("instance", instance.traits), ("static", abc.classes[cls].traits)):
        for trait in traits:
            print(f"  {side:8} {TRAIT_KIND_WORDS.get(trait.base_kind, '?'):8} {abc.name_of(trait.name)}")


if __name__ == "__main__":
    main()
