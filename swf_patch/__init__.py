"""swf_patch: declarative patching of the ActionScript 3 bytecode in SWF and GFX (Scaleform) files.

    import swf_patch
    with open("in.swf", "rb") as f:
        out = swf_patch.apply(f.read(), ["my_patches.yaml"], overrides={"speed": 2})

Patch sets are YAML (see patch-format.md); README.md in this folder maps the modules.
Requires Python 3.9+ and PyYAML.
"""
from .engine import apply, apply_fitting, check, failed
from .errors import PatchError
from .lint import lint
from .patchset import PatchSets

__all__ = ["apply", "apply_fitting", "check", "failed", "lint", "PatchError", "PatchSets"]
