"""Applying patch sets: the top-level flow.

    apply(data, patch sets)                                   a SWF or GFX file
      └─ patch_abc: the bytecode in its DoABC tag
           └─ apply_to_abc: for each active patch, for each operation -> operations.OPERATIONS
      └─ rebuild the file around the patched bytecode

Containers holding several payloads (e.g. redswf_patch) call patch_abc for each.
Nothing is written unless every patch applies: any problem raises PatchError. apply_fitting is
the lenient alternative: it leaves out each patch that doesn't apply, and reports it. Patch sets
are linted first (load).
"""
from __future__ import annotations

import copy
from typing import Any, Collection, Optional

from .abcfile import ABC
from .errors import PatchError
from .lint import lint
from .operations import OPERATIONS, Context
from .patchset import PatchSets
from .swf import read_abc, replace_abc


def load(patchset_paths: list[str]) -> PatchSets:
    """Patch sets, linted first (as the command line does): any problem raises PatchError listing
    them, before anything is applied."""
    problems = lint(patchset_paths)
    if problems:
        raise PatchError(f"{len(problems)} problem(s) in the patch set(s):\n  " + "\n  ".join(problems))
    return PatchSets(patchset_paths)


def apply_to_abc(abc: ABC, sets: PatchSets, values: dict[str, Any], only: Optional[Collection[str]] = None) -> list[str]:
    """Apply the active patches (or only those with these ids) to one ABC. Returns the ids applied."""
    ctx = Context(abc, sets, values)
    applied = []
    for patch in sets.active(values):
        if only is not None and patch.id not in only:
            continue
        for j, op in enumerate(patch.ops):
            kind, body = next(iter(op.items()))
            OPERATIONS[kind](ctx, body, f"patch {patch.id} op {j} ({kind})")
        applied.append(patch.id)
    return applied


def patch_abc(raw_abc: bytes, sets: PatchSets, values: dict[str, Any]) -> bytes:
    """Serialized bytecode with the active patches applied."""
    abc = ABC(raw_abc)
    apply_to_abc(abc, sets, values)
    return abc.serialize()


def check_abc(abc: ABC, sets: PatchSets, values: dict[str, Any]) -> list[tuple[str, str]]:
    """Dry run: for each patch, (id, status), status being "ok", "skipped (condition false)" or
    "FAILS: <reason>". Each patch is tried on top of the ones before it that applied."""
    return _each_that_fits(abc, sets, values)[1]


def patch_abc_fitting(raw_abc: bytes, sets: PatchSets, values: dict[str, Any]) -> tuple[bytes, list[tuple[str, str]]]:
    """Like patch_abc, but a patch that doesn't apply is left out instead of stopping the run: the
    serialized bytecode with the patches that fit, and check_abc's report saying which didn't."""
    abc, report = _each_that_fits(ABC(raw_abc), sets, values)
    return abc.serialize(), report


def _each_that_fits(abc: ABC, sets: PatchSets, values: dict[str, Any]) -> tuple[ABC, list[tuple[str, str]]]:
    """Each active patch applied on a copy, on top of the ones before it that applied, so a patch
    that fails leaves nothing behind: (the result, check_abc's report). abc itself is unchanged."""
    active = {p.id for p in sets.active(values)}
    report = []
    for patch in sets.patches:
        if patch.id not in active:
            report.append((patch.id, "skipped (condition false)"))
            continue
        trial = copy.deepcopy(abc)
        try:
            apply_to_abc(trial, sets, values, only={patch.id})
        except PatchError as e:
            report.append((patch.id, f"FAILS: {e}"))
        else:
            abc = trial
            report.append((patch.id, "ok"))
    return abc, report


def failed(report: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(id, reason) of each patch a check_abc report says doesn't apply."""
    return [(patch_id, status[len("FAILS: "):]) for patch_id, status in report if status.startswith("FAILS")]


def apply(data: bytes, patchset_paths: list[str], profile: Optional[str] = None,
          overrides: Optional[dict[str, Any]] = None) -> bytes:
    """Patch a SWF or GFX file and return the new file."""
    sets = load(patchset_paths)
    values = sets.values(profile, overrides)
    return replace_abc(data, patch_abc(read_abc(data), sets, values))


def apply_fitting(data: bytes, patchset_paths: list[str], profile: Optional[str] = None,
                  overrides: Optional[dict[str, Any]] = None) -> tuple[bytes, list[tuple[str, str]]]:
    """Patch a SWF or GFX file with the patches that apply, leaving out those that don't: the new
    file and check's report (failed() lists what was left out). For files a patch set wasn't
    written for, e.g. another mod's version; apply is the strict one."""
    sets = load(patchset_paths)
    raw, report = patch_abc_fitting(read_abc(data), sets, sets.values(profile, overrides))
    return replace_abc(data, raw), report


def check(data: bytes, patchset_paths: list[str], profile: Optional[str] = None,
          overrides: Optional[dict[str, Any]] = None) -> list[tuple[str, str]]:
    """check_abc on a SWF or GFX file."""
    sets = load(patchset_paths)
    return check_abc(ABC(read_abc(data)), sets, sets.values(profile, overrides))
