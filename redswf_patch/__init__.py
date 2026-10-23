"""redswf_patch: swf_patch for CDPR's .redswf files (REDengine Flash resources, e.g. The Witcher 3's UI).

    import redswf_patch
    with open("in.redswf", "rb") as f:
        out = redswf_patch.apply(f.read(), ["my_patches.yaml"], overrides={"fade": 0.3})

Every copy of the bytecode in the file is patched identically. Patch sets are swf_patch's (see
swf_patch/patch-format.md); README.md in this folder has more. Requires Python 3.9+ and swf_patch.
"""
from swf_patch import PatchError, PatchSets, failed, lint

from .engine import apply, apply_fitting, check
from .redswf import RedSwf, load_abc

__all__ = ["apply", "apply_fitting", "check", "failed", "lint", "load_abc", "PatchError", "PatchSets", "RedSwf"]
