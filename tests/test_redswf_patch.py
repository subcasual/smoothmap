"""Tests for redswf_patch: the .redswf container, and Smooth Map's patch sets applied to The
Witcher 3's real file (the release hash, parameters, both payloads, refusals). Run from the repo
root:

    python -m unittest -v

Tests that need the game's file skip without it (REDkit's depot copy, see tests/common.py).
"""
import copy
import os
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import redswf_patch                                             # noqa: E402
import swf_patch                                                # noqa: E402
from redswf_patch import PatchError, RedSwf                     # noqa: E402
from swf_patch.abcfile import ABC                               # noqa: E402
from swf_patch.engine import apply_to_abc                       # noqa: E402
from swf_patch.instructions import Instruction, decode, encode  # noqa: E402
from swf_patch.names import Names                               # noqa: E402
from swf_patch.patchset import PatchSets                        # noqa: E402
from swf_patch.swf import read_abc, replace_abc                 # noqa: E402
from swf_patch.verify import measure, verify                    # noqa: E402

from tests.common import DIAGNOSTICS, NO_VANILLA, SMOOTHMAP, VANILLA, have_vanilla, read_bytes, sha256   # noqa: E402

# Engine output with the patch set's defaults (your release values). Update deliberately, after
# testing a changed build in game; the failure message prints the new hash.
EXPECTED_RELEASE = "300016ea73f133ed0a8d838878621ab215fcf42c9fa4b36f9fdd048fc9f9bf2f"   # build 20 (confirmed in game, October 16, 2026)


@unittest.skipUnless(have_vanilla(), NO_VANILLA)
class Container(unittest.TestCase):
    def test_redswf_rebuild_is_identical(self):
        data = read_bytes(VANILLA)
        res = RedSwf(data)
        self.assertEqual(res.build(res.gfx, res.swf), data)


@unittest.skipUnless(have_vanilla(), NO_VANILLA)
class Release(unittest.TestCase):
    _results = {}       # (patch sets, parameters) -> output, shared by the tests (bytes: read-only)

    @classmethod
    def setUpClass(cls):
        cls.data = read_bytes(VANILLA)
        cls.abc = ABC(read_abc(RedSwf(cls.data).gfx))

    def apply(self, *sets, **overrides):
        """Patch vanilla; each combination of patch sets and parameters is computed once."""
        key = (tuple(sets) or (SMOOTHMAP,), tuple(sorted(overrides.items())))
        if key not in Release._results:
            Release._results[key] = redswf_patch.apply(self.data, list(key[0]), None, overrides)
        return Release._results[key]

    # ---- output
    def test_release_build(self):
        out = self.apply()
        self.assertEqual(sha256(out), EXPECTED_RELEASE, f"release build changed, new hash {sha256(out)}")
        self.assertEqual(out, redswf_patch.apply(self.data, [SMOOTHMAP], None, {}),   # a real second run
                         "output must be deterministic")
        r = RedSwf(out)
        self.assertEqual(read_abc(r.gfx), read_abc(r.swf), "both payloads patched identically")
        a = ABC(read_abc(r.gfx))
        changed = [y for x, y in zip(self.abc.methods, a.methods)
                   if x.body and y.body and x.body.code != y.body.code]
        self.assertEqual(len(changed), 18, "unexpected number of changed methods")   # +1 in build 19: tiles-for-final-view
        for m in changed:                       # the verifier covers the opcodes our edits produce
            items = decode(m.body.code)
            verify(a, items, m.body.max_stack, m.body.max_scope_depth - m.body.init_scope_depth)

    def test_plain_payloads_patch_like_the_redswf(self):
        """swf_patch on the GFX or SWF alone gives exactly the payload redswf_patch builds."""
        vanilla, release = RedSwf(self.data), RedSwf(self.apply())
        self.assertEqual(swf_patch.apply(vanilla.gfx, [SMOOTHMAP]), release.gfx)
        self.assertEqual(swf_patch.apply(vanilla.swf, [SMOOTHMAP]), release.swf)
        self.assertEqual(swf_patch.check(vanilla.gfx, [SMOOTHMAP]), redswf_patch.check(self.data, [SMOOTHMAP]))

    def test_maxstack_is_exact_for_edited_methods(self):
        a = ABC(read_abc(RedSwf(self.apply()).gfx))
        for i, (x, y) in enumerate(zip(self.abc.methods, a.methods)):
            if x.body and y.body and x.body.code != y.body.code:
                peak, _ = measure(a, decode(y.body.code))
                self.assertEqual(y.body.max_stack, max(x.body.max_stack, peak))

    def test_named_methods_keep_parameter_names(self):
        a = ABC(read_abc(RedSwf(self.apply(SMOOTHMAP, DIAGNOSTICS)).gfx))
        for m in a.methods:
            if m.body:
                named = {it.args[2] + 1 for it in decode(m.body.code)
                         if getattr(it, "op", None) == "debug" and it.args[0] == 1}
                if named:
                    self.assertFalse(set(range(1, len(m.param_types) + 1)) - named)

    def test_our_locals_are_named(self):
        """Every local our patches add carries a debug name (decompilers show it instead of _locN_)."""
        abc = ABC(read_abc(RedSwf(self.apply()).gfx))
        names = set()
        for m in abc.methods:
            if m.body:
                for it in decode(m.body.code):
                    if getattr(it, "op", None) == "debug" and it.args[0] == 1:
                        names.add(abc.string(it.args[1]))
        self.assertTrue({"now", "dt", "budgetStart", "wasVisible", "menu"} <= names, names)

    def test_slide_parameter(self):
        on = ABC(read_abc(RedSwf(self.apply()).gfx))
        off = ABC(read_abc(RedSwf(self.apply(slide=False)).gfx))
        core = lambda a: a.methods[Names(a).method("red.core::CoreMenu.<static-init>")].body.code
        self.assertEqual(core(on), core(self.abc), "slide on: CoreMenu untouched")
        self.assertNotEqual(core(off), core(self.abc), "slide off: CoreMenu changed")

    def test_view_refreshed_after_slide_only_with_slide(self):
        hub = lambda a: a.methods[Names(a).method("HubMap.SetMenuAnimCompleted")].body.code
        on = ABC(read_abc(RedSwf(self.apply()).gfx))
        off = ABC(read_abc(RedSwf(self.apply(slide=False)).gfx))
        self.assertNotEqual(hub(on), hub(self.abc))
        self.assertEqual(hub(off), hub(self.abc))

    def test_diagnostics_apply_on_top(self):
        out = self.apply(SMOOTHMAP, DIAGNOSTICS)
        a = ABC(read_abc(RedSwf(out).gfx))
        hm = Names(a).find_class("HubMap")
        self.assertIn("_smDbg", [a.name_of(t.name) for t in a.instances[hm].traits])
        self.apply(SMOOTHMAP, DIAGNOSTICS, slowmo=True)

    # ---- check report
    def test_check_reports_every_patch_ok(self):
        report = redswf_patch.check(self.data, [SMOOTHMAP])
        self.assertTrue(all(s in ("ok", "skipped (condition false)") for _, s in report), report)

    # ---- leaving out patches that don't fit (compatibility variants)
    def test_patches_that_dont_fit_are_left_out_whole(self):
        """apply_fitting leaves out a patch that fails, including what its earlier ops did, and
        reports it; with every patch fitting it gives exactly apply's bytes."""
        fitting, report = redswf_patch.apply_fitting(self.data, [SMOOTHMAP])
        self.assertEqual(fitting, self.apply())
        self.assertEqual(redswf_patch.failed(report), [])
        with tempfile.TemporaryDirectory() as tmp:
            extra = os.path.join(tmp, "extra.yaml")
            with open(extra, "w", encoding="utf-8") as f:
                f.write(textwrap.dedent("""\
                    format: 1
                    patches:
                      - id: half-fits
                        ops:
                          - add_field: { class: HubMap, name: _smHalf, type: int, visibility: private }
                          - edit:
                              method: HubMap.Zoom
                              steps:
                                - replace: { match: "pushbyte 123" }
                                  with: ""
                    """))
            self.assertEqual(redswf_patch.lint([extra]), [])
            with self.assertRaises(PatchError):
                redswf_patch.apply(self.data, [SMOOTHMAP, extra])
            out, report = redswf_patch.apply_fitting(self.data, [SMOOTHMAP, extra])
        self.assertEqual([patch_id for patch_id, _ in redswf_patch.failed(report)], ["half-fits"])
        self.assertIn("expected 1 match, found 0", redswf_patch.failed(report)[0][1])
        self.assertEqual(out, self.apply())                       # no trace of the field: left out whole

    def test_library_lints_first(self):
        """The library, like the command line, refuses a patch set that doesn't lint, before applying
        anything (here add_field without its required visibility)."""
        with tempfile.TemporaryDirectory() as tmp:
            broken = os.path.join(tmp, "broken.yaml")
            with open(broken, "w", encoding="utf-8") as f:
                f.write("format: 1\npatches:\n  - id: no-visibility\n    ops:\n"
                        "      - add_field: { class: HubMap, name: _smX, type: int }\n")
            for run in (redswf_patch.apply, redswf_patch.apply_fitting, redswf_patch.check):
                with self.assertRaisesRegex(PatchError, "visibility must be one of"):
                    run(self.data, [broken])

    # ---- refusals
    def test_differing_payloads_are_patched_separately(self):
        """Depot files are patched once when both copies of the bytecode match; if they differ,
        each copy gets its own pass (here the source SWF has an extra nop in an unpatched method)."""
        abc = copy.deepcopy(self.abc)
        body = abc.methods[Names(abc).method("HubMapContainer.GetVisibleAreaLocalLeftBottomPos")].body
        items = decode(body.code)
        items.insert(2, Instruction("nop"))
        body.code = encode(items)
        res = RedSwf(self.data)
        mixed = res.build(res.gfx, replace_abc(res.swf, abc.serialize()))
        out = RedSwf(redswf_patch.apply(mixed, [SMOOTHMAP], None, {}))
        self.assertEqual(read_abc(out.gfx), read_abc(RedSwf(self.apply()).gfx))
        sets = PatchSets([SMOOTHMAP])
        apply_to_abc(abc, sets, sets.values())
        self.assertEqual(read_abc(out.swf), abc.serialize())

    def test_double_patch_is_refused(self):
        with self.assertRaises(PatchError):
            redswf_patch.apply(self.apply(), [SMOOTHMAP])


if __name__ == "__main__":
    unittest.main()
