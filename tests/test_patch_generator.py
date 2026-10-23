"""Tests for patch_generator: the source helpers on their own, and export -> edit -> generate on the
game's file with FFDec (skipped without FFDec or the file)."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import patch_generator                                          # noqa: E402
import redswf_patch                                             # noqa: E402
from patch_generator.checks import constants_written   # noqa: E402
from patch_generator.writing import kebab, scalar   # noqa: E402
from swf_patch.instructions import encode                       # noqa: E402
from patch_generator.source import compilable, method_source, methods, stub_unedited   # noqa: E402
from redswf_patch import PatchError, RedSwf                     # noqa: E402
from swf_patch.abcfile import ABC                               # noqa: E402
from swf_patch.instructions import Instruction, decode          # noqa: E402
from swf_patch.names import Names                               # noqa: E402
from swf_patch.swf import read_abc                              # noqa: E402

from tests.common import NO_FFDEC, NO_VANILLA, VANILLA, have_ffdec, have_vanilla   # noqa: E402
HUBMAP = os.path.join("red", "game", "witcher3", "menus", "worldmap", "HubMap.as")
MAPMENU = os.path.join("red", "game", "witcher3", "menus", "worldmap", "MapMenu.as")
CONTAINER = os.path.join("red", "game", "witcher3", "menus", "worldmap", "HubMapContainer.as")


class Source(unittest.TestCase):
    TEXT = (
        "   public class A\n   {\n"
        "      public function A()\n      {\n         super();\n      }\n"
        "      private function f(param1:Number = NaN) : void\n      {\n"
        "         var s:String = \"}{\";  // }\n         /* { */ if(true) { trace('}'); }\n      }\n"
        "      public function get g() : int\n      {\n         return 1;\n      }\n   }\n")

    def test_method_source(self):
        f = method_source(self.TEXT, "A", "f")
        self.assertTrue(f.startswith("private function f(") and f.endswith("}"), f)
        self.assertIn("trace('}');", f)                       # braces in strings and comments skipped
        self.assertEqual(method_source(self.TEXT, "A", "<constructor>").splitlines()[0], "public function A()")
        self.assertIn("return 1;", method_source(self.TEXT, "A", "get g"))
        self.assertIsNone(method_source(self.TEXT, "A", "<static-init>"))
        self.assertIsNone(method_source(self.TEXT, "A", "missing"))

    def test_nan_defaults_compile_as_placeholders(self):
        self.assertIn("param1:Number = 0)", compilable(self.TEXT))

    def test_methods_and_stubs(self):
        self.assertEqual([m.key for m in methods(self.TEXT)], ["A", "f", "get g"])
        edited = self.TEXT.replace("return 1;", "return 2;").replace("\n", "\r\n")     # and CRLF, as editors do
        original, changed = stub_unedited(self.TEXT, edited)
        self.assertEqual(original.count("\n"), self.TEXT.count("\n"))                  # line numbers kept
        self.assertIn("public function A()\n      {super();", original)              # constructors keep super()
        self.assertNotIn("trace", original)                                         # f wasn't edited: stubbed
        self.assertIn("return 2;", changed)                                         # g was: compiled as written
        self.assertIn("return 1;", original)                                         # kept in both versions
        unedited_getter = stub_unedited(self.TEXT, self.TEXT.replace("trace", "trace2"))[0]
        self.assertIn("{return 0;", unedited_getter)                                 # stubs return a default

    def test_patch_ids(self):
        self.assertEqual(kebab("HubMap.handleScrollAndZoomTimer"), "hub-map-handle-scroll-and-zoom-timer")
        self.assertEqual(kebab("HubMap.<constructor>"), "hub-map-constructor")

    def test_yaml_quoted_only_when_needed(self):
        for bare in ("HubMap", "HubMap.<constructor>", "flash.utils::Timer", "void", "_my_newVar"):
            self.assertEqual(scalar(bare), bare)
        for quoted in ("*", "true", "17", "HubMap.x: new field", "a # b"):
            self.assertEqual(scalar(quoted)[0], '"', quoted)


@unittest.skipUnless(have_vanilla(), NO_VANILLA)
class ConstantWrites(unittest.TestCase):
    def test_setproperty_on_a_constant_is_caught(self):
        """Assigning a constant needs initproperty; setproperty fails at run time."""
        with open(VANILLA, "rb") as f:
            abc = ABC(read_abc(RedSwf(f.read()).gfx))
        names = Names(abc)
        hub = names.find_class("HubMap")
        body = abc.methods[names.method("HubMap.<static-init>")].body
        self.assertIsNone(constants_written(abc, hub, body))                       # vanilla: initproperty
        code = decode(body.code)
        for item in code:
            if isinstance(item, Instruction) and item.op == "initproperty" \
                    and abc.name_of(item.args[0]) == "KEYBOARD_SCROLL_SPEED":
                item.op = "setproperty"
        body.code = encode(code)
        self.assertEqual(constants_written(abc, hub, body), "KEYBOARD_SCROLL_SPEED")


@unittest.skipUnless(have_vanilla(), NO_VANILLA)
@unittest.skipUnless(have_ffdec(), NO_FFDEC)
class Generate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(VANILLA, "rb") as f:
            cls.data = f.read()
        cls.gfx = RedSwf(cls.data).gfx
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def export(self, name, classes, edits):
        """Export classes into a new folder and apply text edits: {file: [(old, new)]}."""
        folder = os.path.join(self.tmp, name)
        patch_generator.export(self.gfx, classes, folder, source=VANILLA)
        for file, replacements in edits.items():
            path = os.path.join(folder, file)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for old, new in replacements:
                self.assertIn(old, text)
                text = text.replace(old, new, 1)
            with open(path, "w", encoding="utf-8", newline="\n") as f:    # LF, as some editors save
                f.write(text)
        return folder

    def test_edits_become_verified_patches(self):
        folder = self.export("edits", ["HubMap", "HubMapContainer", "MapMenu"], {
            CONTAINER: [("         return this._visibleAreaLocalLeftBottom;\n",          # a new branch: own label
                         "         if(this._visibleAreaLocalLeftBottom == null)\n         {\n            trace(\"none\");\n"
                         "         }\n         return this._visibleAreaLocalLeftBottom;\n"),
                        ("if(param1 > this._mapScrollMaxY)", "if(param1 >= this._mapScrollMaxY)"),   # landing label
                        # removing an if block deletes the label after it: the whole method, for now
                        ("         if(param1 > -this._mapScrollMinX)\n         {\n            return -this._mapScrollMinX;\n"
                         "         }\n", "")],
            HUBMAP: [("new Timer(17)", "new Timer(16)"),
                     ("this._scrollAndZoomTimer.start();\n", "this._scrollAndZoomTimer.start();\n         this.smWarmUp();\n"),
                     ("            this._scrollCoef = SCROLL_COEF_MIN;\n",               # two new locals
                      "            var smallest:Number = SCROLL_COEF_MIN;\n            var label:String = \"min\";\n"
                      "            this._scrollCoef = smallest;\n"),
                     ("private var _gamepadZoomValue:Number = 0;", "private var _gamepadZoomValue:Number = 60;"),
                     ("KEYBOARD_SCROLL_SPEED:int = 15;", "KEYBOARD_SCROLL_SPEED:int = 25;"),   # a constant
                     # new code using a name the file lacks (Namespace): declared with add_names
                     ("                  if(this.mcHubMapPreview.CanBeToggled())\n",
                      "                  trace(new Namespace(\"zkey\"));\n                  if(this.mcHubMapPreview.CanBeToggled())\n"),
                     ("      protected function handleSpeedTimer(",                      # a new static field
                      "      private static var _smCount:uint = 7;\n\n      protected function handleSpeedTimer("),
                     ("      protected function handleSpeedTimer(",                      # a new field and method
                      "      private var _smReady:Boolean = false;\n\n      private function smWarmUp() : void\n"
                      "      {\n         this._smReady = true;\n      }\n\n      protected function handleSpeedTimer(")],
            # MapMenu's constructor has code FFDec can't compile back; stubbing unedited methods avoids it
            MAPMENU: [("override protected function configUI() : void\n      {\n",
                       "override protected function configUI() : void\n      {\n         trace(\"edited\");\n")]})
        result = patch_generator.generate(self.gfx, folder)
        self.assertEqual(sorted(result.methods), ["HubMap.<constructor>", "HubMap.KEYBOARD_SCROLL_SPEED",
                                                  "HubMap._gamepadZoomValue", "HubMap._smCount", "HubMap._smReady",
                                                  "HubMap.handleInput", "HubMap.handleSpeedTimer", "HubMap.smWarmUp",
                                                  "HubMapContainer.GetRestrictedX", "HubMapContainer.GetRestrictedY",
                                                  "HubMapContainer.GetVisibleAreaLocalLeftBottomPos", "MapMenu.configUI"])
        self.assertIn("- add_names:\n          - public::Namespace", result.yaml)
        # only what changed is patched (anchored on the original's instructions)...
        constructor = result.yaml[result.yaml.index("id: hub-map-constructor"):]
        self.assertIn("- replace:\n                after: |\n                  findpropstrict  Timer\n"
                      "                match: |\n                  pushbyte        17", constructor)
        # ...branches included: new code's own labels, and a landing label for a jump to the original's
        restricted_y = result.yaml[result.yaml.index("id: hub-map-container-get-restricted-y"):]
        self.assertRegex(restricted_y.split("\n  - id:")[0], r"code: \|\n\s+target\d+:\n")
        self.assertIn("target1:", result.yaml[result.yaml.index("id: hub-map-container-get-visible-area"):])
        # ...except where a change can't be placed yet (here: it deletes a label of the original)
        self.assertIn('"HubMapContainer.GetRestrictedX: edited ActionScript (whole method)"', result.yaml)
        self.assertEqual(result.yaml.count('(whole method)"'), 1)                         # titles
        # the class initializer isn't replaced: the constant is a set_initial_values, the new static
        # field's initializer is carried by its add_field
        self.assertIn("KEYBOARD_SCROLL_SPEED: { expect: 15, value: 25 }", result.yaml)
        self.assertIn("_gamepadZoomValue: { expect: 0, value: 60 }", result.yaml)
        self.assertIn("this._scrollAndZoomTimer = new Timer(16);", result.yaml)          # the as3 field
        self.assertIn("locals: [smallest, label]", result.yaml)                         # named after the source
        self.assertIn("value: false", result.yaml)
        self.assertLess(result.yaml.index("add_method"), result.yaml.index("- edit:"))  # new members first
        path = os.path.join(folder, "patch.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(result.yaml)
        self.assertEqual(redswf_patch.lint([path]), [])
        patched = redswf_patch.apply(self.data, [path])                                  # both payloads
        abc = ABC(read_abc(RedSwf(patched).gfx))
        pushes = [i.args[0] for i in decode(abc.methods[Names(abc).method("HubMap.<constructor>")].body.code)
                  if isinstance(i, Instruction) and i.op == "pushbyte"]
        self.assertIn(16, pushes)
        self.assertNotIn(17, pushes)
        names = Names(abc)
        zoom, _ = names.trait(names.find_class("HubMap"), "_gamepadZoomValue")
        self.assertEqual(abc.ints[zoom.value_index] if zoom.value_kind == 0x03 else abc.doubles[zoom.value_index], 60)
        field, _ = names.trait(names.find_class("HubMap"), "_smReady")
        self.assertEqual(abc.namespaces[abc.multinames[field.name][1]].kind, 0x05)          # private
        speed_timer = decode(abc.methods[names.method("HubMap.handleSpeedTimer")].body.code)
        debug_names = {abc.string(i.args[1]) for i in speed_timer if isinstance(i, Instruction) and i.op == "debug"}
        self.assertLessEqual({"smallest", "label"}, debug_names)                          # decompilers show these
        with self.assertRaises(PatchError):                       # the anchor is the whole original method
            redswf_patch.apply(patched, [path])

    def test_more_kinds_of_members(self):
        """Static methods with parameters and defaults, a getter/setter pair, constants, a Vector field,
        initial values added and removed, and an edit to one accessor of a pair."""
        tween = os.path.join("com", "gskinner", "motion", "GTween.as")
        folder = self.export("members", ["HubMap", "HubMapContainer", "com.gskinner.motion::GTween"], {
            HUBMAP: [("      protected function handleSpeedTimer(",
                      "      private static const SM_LIMIT:int = 9;\n\n      private const SM_NAME:String = \"map\";\n\n"
                      "      private static function smScale(x:Number, factor:Number = NaN) : Number\n      {\n"
                      "         return isNaN(factor) ? x : x * factor;\n      }\n\n"
                      "      public function get smZoom() : Number\n      {\n         return this._bufPosX;\n      }\n\n"
                      "      public function set smZoom(value:Number) : void\n      {\n"
                      "         this._bufPosX = value + SM_LIMIT;\n      }\n\n      protected function handleSpeedTimer("),
                     ("private var _inScroll:Boolean;", "private var _inScroll:Boolean = true;"),
                     ("private var _bufPosY:Number = 0;", "private var _bufPosY:Number;")],
            CONTAINER: [("      public function HubMapContainer()\n",
                         "      internal var _smList:Vector.<int>;\n\n      public function HubMapContainer()\n")],
            tween: [("         if(param1 == this._paused)\n", "         trace(\"set\");\n         if(param1 == this._paused)\n")]})
        result = patch_generator.generate(self.gfx, folder)
        for entry in ("HubMap.static smScale", "HubMap.get smZoom", "HubMap.set smZoom", "HubMap.SM_LIMIT",
                      "HubMap.SM_NAME", "HubMap._inScroll", "HubMap._bufPosY", "HubMapContainer._smList",
                      "GTween.set paused"):
            self.assertIn(entry, result.methods)
        for written in ("static: true\n          params:\n            - { name: x, type: Number }\n"
                        "            - { name: factor, type: Number, default: NaN }",
                        "kind: set", "const: true", "type: __AS3__.vec::Vector.<int>", "_inScroll: { value: true }",
                        "_bufPosY: { expect: 0 }", "method: GTween.set paused"):
            self.assertIn(written, result.yaml)
        path = os.path.join(folder, "patch.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(result.yaml)
        redswf_patch.apply(self.data, [path])                                           # both payloads

    def test_patches_combine_with_smooth_map(self):
        """Anchored patches touch only what was edited, so they apply with Smooth Map, which changes the
        same methods elsewhere, in either order (a whole-method replacement couldn't)."""
        folder = self.export("combine", ["HubMap", "MapMenu"], {
            HUBMAP: [("         _defaultScale = 1;\n", "         _defaultScale = 1;\n         trace(\"constructed\");\n")],
            MAPMENU: [("override protected function configUI() : void\n      {\n",
                       "override protected function configUI() : void\n      {\n         trace(\"configured\");\n")]})
        result = patch_generator.generate(self.gfx, folder)
        self.assertNotIn('(whole method)"', result.yaml)
        path = os.path.join(folder, "patch.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(result.yaml)
        smooth_map = os.path.join(ROOT, "patches", "smoothmap.yaml")
        for order in ([smooth_map, path], [path, smooth_map]):
            redswf_patch.apply(self.data, order)                    # raises if either doesn't apply

    def test_unsupported_edits_are_refused(self):
        folder = self.export("refused", ["HubMap", "HubMapContainer"], {
            HUBMAP: [("            this._scrollCoef = SCROLL_COEF_MIN;\n",
                      "            try { this._scrollCoef = SCROLL_COEF_MIN; } catch(e:Error) { }\n"),
                     ("private var _gamepadZoomValue:Number = 0;", "private var _gamepadZoomValue:int = 0;")]})
        with open(os.path.join(folder, CONTAINER), encoding="utf-8") as f:
            text = f.read()
        brace = text.index("{", text.index("public class HubMapContainer")) + 1
        with open(os.path.join(folder, CONTAINER), "w", encoding="utf-8") as f:
            f.write(text[:brace] + "\n      private function smExtra(...rest) : void\n      {\n      }\n" + text[brace:])
        with self.assertRaises(patch_generator.GeneratorError) as caught:
            patch_generator.generate(self.gfx, folder)
        message = str(caught.exception)
        self.assertIn("HubMap.handleSpeedTimer: try/catch isn't supported yet", message)
        self.assertIn("HubMapContainer.smExtra: new methods using `arguments`, rest parameters", message)
        self.assertIn("HubMap._gamepadZoomValue: changing a member's type or visibility isn't supported yet", message)

    def test_nothing_edited_or_another_file(self):
        folder = self.export("unedited", ["HubMap"], {})
        with self.assertRaisesRegex(patch_generator.GeneratorError, "no class has been edited"):
            patch_generator.generate(self.gfx, folder)
        with self.assertRaisesRegex(patch_generator.GeneratorError, "isn't the file"):
            patch_generator.generate(RedSwf(self.data).swf, folder)
        with self.assertRaisesRegex(patch_generator.GeneratorError, "already holds exported classes"):
            patch_generator.export(self.gfx, ["HubMap"], folder)


if __name__ == "__main__":
    unittest.main()
