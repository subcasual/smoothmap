"""Tests for swf_patch on its own: patch sets and lint, the verifier, lossless reading and writing
of the bytecode, and the engine's refusals, at the bytecode level (no container). Run from the
repo root:

    python -m unittest -v

Tests that need the game's file skip without it (REDkit's depot copy, see tests/common.py); its
payloads are taken out with redswf_patch, the only use of it here.
"""
import copy
import math
import os
import re
import sys
import tempfile
import textwrap
import types
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import swf_patch                                                # noqa: E402
from redswf_patch import RedSwf                                 # noqa: E402
from swf_patch import PatchError                                # noqa: E402
from swf_patch.abcfile import ABC, MULTINAME_L, QNAME           # noqa: E402
from swf_patch.assembler import Assembler                       # noqa: E402
from swf_patch.engine import apply_to_abc                       # noqa: E402
from swf_patch.instructions import OPCODES, Instruction, Label, decode, encode  # noqa: E402
from swf_patch.listing import disassemble                       # noqa: E402
from swf_patch.names import Names                               # noqa: E402
from swf_patch.patchset import PatchSets, evaluate_when, make_labels_private  # noqa: E402
from swf_patch.swf import read_abc, replace_abc                 # noqa: E402
from swf_patch.verify import VerifyError, measure, stack_effect, verify  # noqa: E402

from tests.common import DIAGNOSTICS, NO_VANILLA, SMOOTHMAP, VANILLA, have_vanilla, read_bytes   # noqa: E402


class Format(unittest.TestCase):
    def test_patch_sets_lint_clean(self):
        self.assertEqual(swf_patch.lint([SMOOTHMAP, DIAGNOSTICS]), [])

    def test_when_expressions(self):
        v = {"a": True, "b": False}
        self.assertTrue(evaluate_when("a and not b", v))
        self.assertTrue(evaluate_when("b or (a and not b)", v))
        self.assertFalse(evaluate_when("not a", v))
        with self.assertRaises(PatchError):
            evaluate_when("c", v)

    def test_lint_catches_mistakes(self):
        bad = textwrap.dedent("""\
            parameters:
              fade: { default: 0.2 }
            patches:
              - id: broken
                ops:
                  - edit:
                      method: HubMap.configUI
                      steps:
                        - insert: { at: method-start }
                          code: |
                            getlocal        dt
                            push            {{fadee}}
                            jump            done
                          done:
                  - frobnicate: {}
            """)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(bad)
            problems = "\n".join(swf_patch.lint([path]))
        for expected in ("unexpected key 'done' (an escaped label?", "'dt' is not a declared local",
                         "undeclared placeholder '{{fadee}}'", "jump to undefined label 'done'",
                         "unknown operation"):
            self.assertIn(expected, problems)

    def test_labels_made_private_per_copy(self):
        text = "again:\n    iftrue  again   # loop\n    jump    out\nout:"
        private = make_labels_private(text).splitlines()
        suffix = private[0][len("again"):-1]
        self.assertTrue(suffix)
        self.assertEqual(private, [f"again{suffix}:", f"iftrue again{suffix}", f"jump out{suffix}", f"out{suffix}:"])

    def test_encoder_refuses_out_of_range_pushbyte(self):
        with self.assertRaises(ValueError):
            encode([Instruction("pushbyte", 1000), Instruction("returnvoid")])


class Verifier(unittest.TestCase):
    """The stack/scope checks, on small hand-written code (no game file needed)."""
    # Just enough of an ABC for stack_effect: multiname 1 is a plain QName, 2 a run-time MultinameL.
    abc = types.SimpleNamespace(multinames=[None, (QNAME, 0, 0), (MULTINAME_L, 0)])

    def test_every_opcode_has_a_stack_effect(self):
        for name, operands in OPCODES.values():
            args = [Label() if e == "jump" else 1 for e in operands]
            if operands == ("switch",):
                args = [Label(), [Label()]]
            stack_effect(self.abc, Instruction(name, *args))      # raises VerifyError if unknown

    def test_measure_follows_both_branches(self):
        skip = Label()
        code = [Instruction("pushtrue"), Instruction("iffalse", skip),
                Instruction("pushbyte", 1), Instruction("pushbyte", 2), Instruction("pop"), Instruction("pop"),
                skip, Instruction("getlocal0"), Instruction("pushscope"), Instruction("returnvoid")]
        self.assertEqual(measure(self.abc, code), (2, 1))

    def test_runtime_names_take_stack_values(self):
        self.assertEqual(stack_effect(self.abc, Instruction("getproperty", 1)), (1, 1))
        self.assertEqual(stack_effect(self.abc, Instruction("getproperty", 2)), (2, 1))   # object, name

    def test_refusals(self):
        join = Label()
        cases = {
            "underflow": [Instruction("pop"), Instruction("returnvoid")],
            "inconsistent join": [Instruction("pushtrue"), Instruction("iftrue", join), Instruction("pushnull"),
                                  join, Instruction("returnvoid")],
            "max_stack": [Instruction("pushnull"), Instruction("pushnull"), Instruction("returnvoid")],
        }
        for case, code in cases.items():
            with self.subTest(case), self.assertRaises(VerifyError):
                verify(self.abc, code, max_stack=1, max_scope=0)


@unittest.skipUnless(have_vanilla(), NO_VANILLA)
class Payloads(unittest.TestCase):
    """Reading and writing the bytecode must be lossless before any patch is trusted."""
    @classmethod
    def setUpClass(cls):
        cls.res = RedSwf(read_bytes(VANILLA))

    def test_abc_roundtrip_is_identical(self):
        for blob in (self.res.gfx, self.res.swf):
            raw = read_abc(blob)
            self.assertEqual(ABC(raw).serialize(), raw)

    def test_every_method_body_roundtrips(self):
        abc = ABC(read_abc(self.res.gfx))
        bad = [i for i, m in enumerate(abc.methods)
               if m.body and encode(decode(m.body.code)) != m.body.code]
        self.assertEqual(bad, [])

    def test_replace_abc_identity(self):
        for blob in (self.res.gfx, self.res.swf):
            abc = read_abc(blob)
            self.assertEqual(read_abc(replace_abc(blob, abc)), abc)

    def test_disassembly_assembles_back(self):
        """A method's disassembly assembles back to the same code (debug instructions aside), adding
        nothing to the pools, whenever the syntax can express it: no operand shown as #n, no jump
        into the middle of an instruction, no instruction the assembler refuses (newclass, ...)."""
        abc = ABC(read_abc(self.res.gfx))
        names = Names(abc)
        pools = [len(pool) for pool in (abc.strings, abc.ints, abc.uints, abc.doubles, abc.multinames)]
        checked, different = 0, []
        for index, method in enumerate(abc.methods):
            if not method.body:
                continue
            text = "\n".join(disassemble(abc, index, names=names)[1:])
            if re.search(r"\s#\d", text):
                continue
            assembler = Assembler(abc, names, where=f"method {index}", context=names.class_of_method(index))
            try:
                code = assembler.assemble(text)
                assembler.check_labels()
            except PatchError:
                continue
            original = [i for i in decode(method.body.code) if not (isinstance(i, Instruction) and i.op.startswith("debug"))]
            checked += 1
            if encode(code) != encode(original):
                different.append(index)
        self.assertEqual(different, [])
        self.assertGreater(checked, 5000)                     # of 5713 methods with a body
        self.assertEqual([len(pool) for pool in (abc.strings, abc.ints, abc.uints, abc.doubles, abc.multinames)], pools)


@unittest.skipUnless(have_vanilla(), NO_VANILLA)
class Engine(unittest.TestCase):
    """Patch sets applied to the game's bytecode (apply_to_abc): anchors and expected values that
    don't match are refused; names and assembly resolve as the format says."""
    @classmethod
    def setUpClass(cls):
        cls.abc = ABC(read_abc(RedSwf(read_bytes(VANILLA)).gfx))

    # ---- refusals
    def test_unexpected_constant_is_refused(self):
        """Diagnostics' slow motion expects Smooth Map's fade; a different one must be refused."""
        sets = PatchSets([SMOOTHMAP, DIAGNOSTICS])
        abc = copy.deepcopy(self.abc)
        apply_to_abc(abc, sets, sets.values(None, {"fade": 0.3}), only={p.id for p in sets.patches[:-1]})
        with self.assertRaises(PatchError):
            apply_to_abc(abc, sets, sets.values(None, {"slowmo": True, "fade": 0.2}), only={"diag-slow-motion"})

    def test_moved_pattern_is_refused(self):
        abc = copy.deepcopy(self.abc)
        body = abc.methods[Names(abc).method("HubMap.<constructor>")].body
        items = decode(body.code)
        for it in items:                       # a mod that changed Timer(17) -> Timer(25)
            if getattr(it, "op", None) == "pushbyte" and it.args == [17]:
                it.args = [25]
        body.code = encode(items)
        sets = PatchSets([SMOOTHMAP])
        with self.assertRaises(PatchError):
            apply_to_abc(abc, sets, sets.values())

    def test_frame_based_tween_is_refused(self):
        abc = copy.deepcopy(self.abc)
        body = abc.methods[Names(abc).method("HubMap.<constructor>")].body
        items = decode(body.code)
        items.insert(2, Instruction("pop"))
        items.insert(2, Instruction("pushstring", abc.add_string("useFrames")))
        body.code = encode(items)
        sets = PatchSets([SMOOTHMAP])
        with self.assertRaises(PatchError):
            apply_to_abc(abc, sets, sets.values())

    def test_private_names_resolve_through_their_class(self):
        names = Names(self.abc)
        trait, _ = names.trait(names.find_class("HubMap"), "_snapTween2")
        self.assertEqual(names.operand("HubMap._snapTween2"), trait.name)
        with self.assertRaises(PatchError):
            names.operand("_snapTween2")          # a private field is not a public name

    def test_assembler_rules(self):
        asm = Assembler(self.abc, Names(self.abc), {"dt": 4}, where="test")
        code = asm.assemble("getlocal dt\npush 1000\npush 0.5\npush 7")
        self.assertEqual([i.op for i in code], ["getlocal", "pushshort", "pushdouble", "pushbyte"])
        with self.assertRaises(PatchError):
            asm.assemble("getlocal dtt")
        with self.assertRaises(PatchError):
            asm.assemble("pushbyte 1000")

    def test_initial_values_stored_with_fields(self):
        """set_initial_values changes a value stored with a field (and checks the expected one);
        constants set in the class initializer are covered by the release build (set_constants)."""
        patch_set = textwrap.dedent("""\
            format: 1
            parameters:
              zoom: { default: 60 }
            patches:
              - id: values
                ops:
                  - set_initial_values:
                      class: HubMap
                      values:
                        _gamepadZoomValue: { expect: 0, value: "{{zoom}}" }
            """)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "values.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(patch_set)
            self.assertEqual(swf_patch.lint([path]), [])
            sets = PatchSets([path])
        abc = copy.deepcopy(self.abc)
        apply_to_abc(abc, sets, sets.values())
        names = Names(abc)
        trait, _ = names.trait(names.find_class("HubMap"), "_gamepadZoomValue")
        self.assertEqual((trait.value_kind, abc.ints[trait.value_index]), (0x03, 60))
        with self.assertRaisesRegex(PatchError, "_gamepadZoomValue is 60, expected 0"):
            apply_to_abc(abc, sets, sets.values())                     # already changed

    def test_more_kinds_of_members(self):
        """Static methods, getter/setter pairs, parameters with names and defaults, constants, Vector
        types, initial values added and removed, and edits addressed to one accessor of a pair."""
        patch_set = textwrap.dedent("""\
            format: 1
            patches:
              - id: members
                ops:
                  - add_method:
                      class: HubMap
                      name: smTwice
                      static: true
                      params: [{ name: x, type: Number, default: NaN }, { name: times, type: int, default: 2 }]
                      returns: Number
                      code: |
                        getlocal0
                        pushscope
                        getlocal        x
                        getlocal        times
                        multiply
                        returnvalue
                  - add_method:
                      class: HubMap
                      name: smLevel
                      kind: get
                      returns: int
                      code: |
                        getlocal0
                        pushscope
                        pushbyte        3
                        returnvalue
                  - add_method:
                      class: HubMap
                      name: smLevel
                      kind: set
                      params: [{ name: value, type: int }]
                      code: |
                        getlocal0
                        pushscope
                        returnvoid
                  - add_field: { class: HubMap, name: SM_LIMIT, type: int, visibility: private, static: true, const: true, value: 9 }
                  - add_field: { class: HubMap, name: _smList, type: "__AS3__.vec::Vector.<int>", visibility: private }
                  - set_initial_values:
                      class: HubMap
                      values:
                        _gamepadZoomValue: { expect: 0 }
                  - set_initial_values:
                      class: HubMap
                      values:
                        _gamepadZoomValue: { value: 5 }
                  - edit:
                      method: com.gskinner.motion::GTween.set paused
                      steps:
                        - insert: { at: method-start }
                          code: |
                            nop
            """)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "members.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(patch_set)
            self.assertEqual(swf_patch.lint([path]), [])
            sets = PatchSets([path])
        abc = copy.deepcopy(self.abc)
        apply_to_abc(abc, sets, sets.values())
        names = Names(abc)
        hub = names.find_class("HubMap")
        twice = abc.methods[names.method("HubMap.static smTwice")]
        nan, two = twice.optional_values                                  # NaN as a word, for a Number
        self.assertEqual((len(twice.param_types), twice.flags & 0x08, abc.ints[two[0]]), (2, 0x08, 2))
        self.assertTrue(math.isnan(abc.doubles[nan[0]]))
        getter, setter = names.method("HubMap.get smLevel"), names.method("HubMap.set smLevel")
        self.assertNotEqual(getter, setter)
        limit = next(t for t in abc.classes[hub].traits if abc.name_of(t.name) == "SM_LIMIT")
        self.assertEqual((limit.base_kind, abc.ints[limit.value_index]), (6, 9))                  # a constant
        vector = names.trait(hub, "_smList")[0]
        self.assertEqual(abc.multinames[vector.type][0], 0x1D)                                   # TypeName
        zoom = names.trait(hub, "_gamepadZoomValue")[0]
        self.assertEqual(abc.ints[zoom.value_index], 5)                         # removed, then added again
        paused = decode(abc.methods[names.method("com.gskinner.motion::GTween.set paused")].body.code)
        self.assertIn("nop", [i.op for i in paused if isinstance(i, Instruction)])

    def test_new_names_must_be_declared(self):
        """Code can only use names the file has; add_names declares new ones (qualified), once."""
        def patch_set(declare):
            return textwrap.dedent(f"""\
                format: 1
                patches:
                  - id: new-names
                    ops:
                      {declare}
                      - add_method:
                          class: HubMap
                          name: smMakeNamespace
                          code: |
                            getlocal0
                            pushscope
                            findpropstrict  Namespace
                            pushstring      ""
                            constructprop   Namespace, 1
                            pop
                            getlex          flash.geom::SmNewThing
                            pop
                            returnvoid
                """)

        def run(declare):
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "names.yaml")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(patch_set(declare))
                problems = swf_patch.lint([path])
                sets = PatchSets([path]) if not problems else None
            abc = copy.deepcopy(self.abc)
            if sets:
                apply_to_abc(abc, sets, sets.values())
            return abc, problems

        with self.assertRaisesRegex(PatchError, "no public name or class 'Namespace'"):
            run("- capture: { method: HubMap.showMap, pattern: getlocal0, occurrence: first }")
        declare = "- add_names: [public::Namespace, flash.geom::SmNewThing, public::Namespace]"
        abc, problems = run(declare)
        self.assertEqual(problems, [])
        self.assertEqual(sum(1 for m in abc.multinames if m and m[0] == QNAME and abc.string(m[2]) == "Namespace"), 1)
        abc.find_qname("flash.geom", "SmNewThing")                                    # raises if missing
        _, problems = run("- add_names: [Namespace]")
        self.assertIn("new names are written qualified", "\n".join(problems))

    def test_methods_with_try_catch_are_refused(self):
        """Exception handlers address code by byte offset: an edit would leave them pointing at the
        wrong instructions, so it's refused rather than written."""
        patch_set = textwrap.dedent("""\
            format: 1
            patches:
              - id: edit-try-catch
                ops:
                  - edit:
                      method: PinPointersManager.createPointer
                      steps:
                        - insert: { at: method-start }
                          code: nop
            """)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "try.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(patch_set)
            self.assertEqual(swf_patch.lint([path]), [])
            sets = PatchSets([path])
        with self.assertRaisesRegex(PatchError, "try/catch"):
            apply_to_abc(copy.deepcopy(self.abc), sets, sets.values())

    def test_new_members_with_values_and_visibility(self):
        patch_set = textwrap.dedent("""\
            format: 1
            patches:
              - id: new-members
                ops:
                  - add_field: { class: HubMap, name: _t1, type: Boolean, visibility: private, value: true }
                  - add_field: { class: HubMap, name: _t2, type: "*", visibility: private, value: null }
                  - add_field: { class: HubMap, name: _t3, type: String, visibility: protected, value: "hi" }
                  - add_field: { class: HubMap, name: _t4, type: Number, visibility: private }
                  - add_field: { class: HubMapContainer, name: _t5, type: uint, visibility: private, static: true, value: 7 }
                  - add_method:
                      class: HubMap
                      name: tMethod
                      visibility: private
                      code: |
                        getlocal0
                        pushscope
                        returnvoid
            """)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "members.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(patch_set)
            self.assertEqual(swf_patch.lint([path]), [])
            sets = PatchSets([path])
        abc = copy.deepcopy(self.abc)
        apply_to_abc(abc, sets, sets.values())
        names = Names(abc)
        hub = names.find_class("HubMap")
        trait = lambda member: names.trait(hub, member)[0]                                  # noqa: E731
        namespace_kind = lambda member: abc.namespaces[abc.multinames[trait(member).name][1]].kind   # noqa: E731
        self.assertEqual((trait("_t1").value_kind, trait("_t2").value_kind), (0x0B, 0x0C))     # true, null
        self.assertEqual((abc.string(trait("_t3").value_index), trait("_t2").type), ("hi", 0))
        self.assertEqual(trait("_t4").value_index, 0)                                          # no initial value
        self.assertEqual((namespace_kind("_t1"), namespace_kind("_t3"), namespace_kind("tMethod")), (0x05, 0x18, 0x05))
        self.assertEqual(abc.methods[trait("tMethod").method].return_type, 0)                 # returns: * (default)


if __name__ == "__main__":
    unittest.main()
