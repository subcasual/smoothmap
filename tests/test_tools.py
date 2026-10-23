"""Smoke tests of the inspection tools (tools/disasm.py, diff.py, as3diff.py --list, extract.py) and
swf_patch's command line, run as a user would, on vanilla and on the release build. Skipped without
the game's file."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import redswf_patch                                             # noqa: E402
from redswf_patch import RedSwf, load_abc                       # noqa: E402
from swf_patch.listing import method_names                      # noqa: E402

from tests.common import NO_VANILLA, SMOOTHMAP, VANILLA, have_vanilla, read_bytes   # noqa: E402


def tool(name, *args):
    result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", name), *args],
                            capture_output=True, text=True, encoding="utf-8")
    return result.returncode, result.stdout + result.stderr


@unittest.skipUnless(have_vanilla(), NO_VANILLA)
class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patched = os.path.join(cls.tmp.name, "patched.redswf")
        with open(cls.patched, "wb") as f:
            f.write(redswf_patch.apply(read_bytes(VANILLA), [SMOOTHMAP]))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_disasm(self):
        code, out = tool("disasm.py", VANILLA, "--classes", "worldmap::HubMap")
        self.assertIn("red.game.witcher3.menus.worldmap::HubMap\n", out)
        code, out = tool("disasm.py", "vanilla", "HubMap")       # the tools' name for the depot copy
        self.assertIn("function handleShowTimer", out)
        code, out = tool("disasm.py", VANILLA, "red.core::CoreMenu", "<static-init>")
        self.assertEqual(code, 0, out)
        self.assertIn('pushstring      "Core.Menu.Current.Module.Invalidate"', out)   # patch syntax: double quotes
        code, out = tool("disasm.py", VANILLA, "HubMap", "nosuch")
        self.assertNotEqual(code, 0)
        self.assertIn("member 'nosuch' of HubMap: 0 matches", out)

    def test_diff_matches_methods_by_name(self):
        code, out = tool("diff.py", VANILLA, self.patched)
        self.assertEqual(code, 0, out)
        self.assertIn("== Methods changed: 18, added: 1", out)
        self.assertIn("  + red.game.witcher3.menus.worldmap::HubMapContainer.smSlideOffset", out)
        self.assertIn("  + com.gskinner.motion::GTween.static _smTicker: Timer", out)

    def test_as3diff_lists_changed_classes(self):
        code, out = tool("as3diff.py", "--list", VANILLA, self.patched)
        self.assertEqual(code, 0, out)
        self.assertIn("red.game.witcher3.menus.worldmap.HubMapContainer", out)

    def test_extract(self):
        out = os.path.join(self.tmp.name, "inside.swf")
        code, text = tool("extract.py", self.patched, "--swf", "-o", out)
        self.assertEqual(code, 0, text)
        with open(out, "rb") as f:
            self.assertEqual(f.read(3), b"CWS")                # the depot file's source SWF

    def test_swf_patch_command_line_on_a_plain_swf(self):
        swf, out = os.path.join(self.tmp.name, "vanilla.swf"), os.path.join(self.tmp.name, "patched.swf")
        with open(swf, "wb") as f:
            f.write(RedSwf(read_bytes(VANILLA)).swf)
        result = subprocess.run([sys.executable, "-m", "swf_patch", "apply", "-p", SMOOTHMAP, swf, out],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_bytes(out), RedSwf(read_bytes(self.patched)).swf)
        code, text = tool("disasm.py", out, "HubMapContainer", "smSlideOffset")   # the tools read plain files too
        self.assertEqual(code, 0, text)

    def test_method_names_are_unique(self):
        names = list(method_names(load_abc(VANILLA)).values())
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
