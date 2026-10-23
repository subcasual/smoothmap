"""Applying patch sets to a .redswf: every copy of the bytecode in it, patched identically.

    apply(data, patch sets)
      └─ for each payload (GFX; source SWF in depot files): swf_patch's patch_abc
      └─ rebuild the file around the patched payloads

Nothing is written unless every patch applies: any problem raises PatchError. apply_fitting is
the lenient alternative: it leaves out each patch that doesn't apply (in every copy), and reports it.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from swf_patch.abcfile import ABC
from swf_patch.engine import apply_to_abc, check_abc, failed, load, patch_abc, patch_abc_fitting
from swf_patch.swf import read_abc, replace_abc

from .redswf import RedSwf


def apply(data: bytes, patchset_paths: list[str], profile: Optional[str] = None,
          overrides: Optional[dict[str, Any]] = None) -> bytes:
    """Patch a .redswf (depot or cooked) and return the new file."""
    sets = load(patchset_paths)
    values = sets.values(profile, overrides)
    resource = RedSwf(data)
    new_gfx_abc = patch_abc(read_abc(resource.gfx), sets, values)
    return _rebuild(resource, new_gfx_abc, lambda raw: patch_abc(raw, sets, values))


def apply_fitting(data: bytes, patchset_paths: list[str], profile: Optional[str] = None,
                  overrides: Optional[dict[str, Any]] = None) -> tuple[bytes, list[tuple[str, str]]]:
    """Patch a .redswf with the patches that apply to its GFX payload, leaving out those that don't:
    the new file and check's report (swf_patch.engine.failed lists what was left out). A depot
    file's source SWF gets exactly the same patches. For files a patch set wasn't written for, e.g.
    another mod's version; apply is the strict one."""
    sets = load(patchset_paths)
    values = sets.values(profile, overrides)
    resource = RedSwf(data)
    new_gfx_abc, report = patch_abc_fitting(read_abc(resource.gfx), sets, values)
    left_out = {patch_id for patch_id, _ in failed(report)}
    applied = [p.id for p in sets.active(values) if p.id not in left_out]

    def same_patches(raw: bytes) -> bytes:              # strict: a copy differing from the GFX's still fails loudly
        abc = ABC(raw)
        apply_to_abc(abc, sets, values, only=applied)
        return abc.serialize()

    return _rebuild(resource, new_gfx_abc, same_patches), report


def _rebuild(resource: RedSwf, new_gfx_abc: bytes, patch_other: Callable[[bytes], bytes]) -> bytes:
    """The file with its patched GFX bytecode, and in depot files its source SWF's (patch_other)."""
    new_swf = None
    if not resource.cooked:
        gfx_abc, swf_abc = read_abc(resource.gfx), read_abc(resource.swf)
        # Depot files carry the same bytecode twice: patch it once. Copies that differ get their own pass.
        new_swf_abc = new_gfx_abc if swf_abc == gfx_abc else patch_other(swf_abc)
        new_swf = replace_abc(resource.swf, new_swf_abc)
    return resource.build(replace_abc(resource.gfx, new_gfx_abc), new_swf)


def check(data: bytes, patchset_paths: list[str], profile: Optional[str] = None,
          overrides: Optional[dict[str, Any]] = None) -> list[tuple[str, str]]:
    """swf_patch's check_abc on the GFX payload (what the game runs)."""
    sets = load(patchset_paths)
    return check_abc(ABC(read_abc(RedSwf(data).gfx)), sets, sets.values(profile, overrides))
