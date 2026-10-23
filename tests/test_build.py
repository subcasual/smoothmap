"""Packaging pipeline tests (tools/build_mod.py) against the wcc_lite stand-in (tests/fake_wcc_lite.py).
They show the build verifies every stage: the real wcc_lite can't run here."""
import json
import os
import shutil
import subprocess
import zipfile
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from tests.common import NO_FFDEC, NO_VANILLA, SMOOTHMAP, VANILLA, have_ffdec, have_vanilla   # noqa: E402


@unittest.skipUnless(have_vanilla(), NO_VANILLA)
class Build(unittest.TestCase):
    def build(self, break_mode="", extra_args=(), mods=None, as3diff=False, unbundler="builtin", compat_extra=None):
        """mods: {folder: (modName or None, map file)} to build compatibility variants for; map file:
        False (none), True (loose), or "bundle" (in content/blob0.bundle). compat_extra: a patch
        set's text, applied to the variants after Smooth Map."""
        tmp = tempfile.mkdtemp()
        with open(os.path.join(ROOT, "build.yaml")) as f:
            cfg = yaml.safe_load(f)
        fake = [sys.executable, os.path.join(ROOT, "tests", "fake_wcc_lite.py")]
        cfg["commands"] = {k: fake + v[1:] for k, v in cfg["commands"].items()}
        cfg["redkit"] = {"bin": ""}
        cfg["work"], cfg["dist"] = os.path.join(tmp, "work"), os.path.join(tmp, "dist")
        cfg["targets"] = {"release": dict(cfg["targets"]["release"], input=VANILLA,
                                          patches=[SMOOTHMAP])}
        cfg["as3diff"] = os.path.join(tmp, "changes") if as3diff else None
        cfg["unbundler"] = unbundler
        if mods is None:
            del cfg["compat"]
        else:
            cfg["compat"].update(mods=os.path.join(tmp, "mods"), patches=list(cfg["targets"]["release"]["patches"]))
            if compat_extra:
                extra = os.path.join(tmp, "extra.yaml")
                with open(extra, "w", encoding="utf-8") as f:
                    f.write(compat_extra)
                cfg["compat"]["patches"].append(extra)
            for folder, (mod_name, has_map) in mods.items():
                content = os.path.join(tmp, "mods", folder, "content")
                os.makedirs(content)
                if mod_name:
                    with open(os.path.join(content, "info.json"), "w") as f:
                        json.dump({"modName": mod_name, "version": "2.0"}, f)
                if has_map == "bundle":     # packed by the stand-in, from a depot-path layout
                    staged = os.path.join(tmp, "stage", folder, *cfg["resource"].split("\\"))
                    os.makedirs(os.path.dirname(staged))
                    shutil.copy(VANILLA, staged)
                    subprocess.run(fake + ["pack", f"-dir={os.path.join(tmp, 'stage', folder)}", f"-outdir={content}"],
                                   check=True)
                elif has_map:               # loose depot file: gets cooked, like the release
                    shutil.copy(VANILLA, content)
        path = os.path.join(tmp, "build.yaml")
        with open(path, "w") as f:
            yaml.safe_dump(cfg, f)
        env = dict(os.environ, FAKE_WCC_BREAK=break_mode)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_mod.py"), "--config", path, *extra_args],
                           capture_output=True, text=True, env=env)
        return r, cfg

    def test_full_build(self):
        r, cfg = self.build()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("byte-identical to the cooked file", r.stdout)
        zip_name = cfg["targets"]["release"]["zip"].format(version=cfg["info_json"]["version"])
        zip_path = os.path.join(cfg["dist"], zip_name)
        self.assertTrue(os.path.exists(zip_path))
        with zipfile.ZipFile(zip_path) as z:
            info = json.loads(z.read(f"{cfg['targets']['release']['mod_folder']}/content/info.json"))
        self.assertEqual(info, cfg["info_json"])                 # exactly the configured values

    def test_content_hash_printed_and_stable(self):
        r, cfg = self.build()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from build_mod import content_hash
        mod_dir = os.path.join(cfg["dist"], "release", cfg["targets"]["release"]["mod_folder"])
        self.assertIn(f"content:    {content_hash(mod_dir)[:16]}", r.stdout)
        # depends on names and contents only: not timestamps, not Vortex's marker file
        bundle = os.path.join(mod_dir, "content", "blob0.bundle")
        before = content_hash(mod_dir)
        os.utime(bundle, (0, 0))
        with open(os.path.join(mod_dir, "content", "__folder_managed_by_vortex"), "w") as f:
            f.write("vortex")
        self.assertEqual(content_hash(mod_dir), before)
        with open(bundle, "ab") as f:
            f.write(b"\0")
        self.assertNotEqual(content_hash(mod_dir), before)

    def test_compat_variant_per_mod(self):
        r, cfg = self.build(mods={"modOther": ("Other Map: Deluxe!", True),
                                  "modNoInfo": (None, True),
                                  "modUnrelated": ("Unrelated", False)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skipped: modUnrelated", r.stdout)
        for name, folder in (("OtherMapDeluxe", "mod000SmoothMap_OtherMapDeluxe"),
                             ("NoInfo", "mod000SmoothMap_NoInfo")):
            with zipfile.ZipFile(os.path.join(cfg["dist"], f"{folder}.zip")) as z:
                self.assertIn(f"{folder}/content/blob0.bundle", z.namelist())
                info = json.loads(z.read(f"{folder}/content/info.json"))
            self.assertEqual(info["name"], f"smoothmap_{name}")
        self.assertEqual(info["modName"], "Smooth Map for NoInfo")
        self.assertTrue(os.path.exists(os.path.join(cfg["dist"], "modSmoothMap.zip")))
        self.assertIn("3 of 3 built", r.stdout)

    def test_variant_leaves_out_patches_that_dont_fit(self):
        """A compatibility variant is built without the patches that don't apply to the other mod's
        file, and the build says which; the release still needs every patch."""
        never_fits = ("format: 1\npatches:\n  - id: never-fits\n    ops:\n      - edit:\n"
                      "          method: HubMap.Zoom\n          steps:\n"
                      "            - replace: { match: \"pushbyte 123\" }\n              with: \"\"\n")
        r, cfg = self.build(mods={"modOther": ("Other", True)}, compat_extra=never_fits)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("LEFT OUT never-fits: ", r.stdout)
        self.assertIn("Built WITHOUT some patches", r.stdout)
        self.assertIn("  Other: never-fits", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(cfg["dist"], "mod000SmoothMap_Other.zip")))

    def test_both_unbundlers_give_the_same_variant(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from build_mod import content_hash
        hashes = {}
        for unbundler in ("builtin", "wcc_lite"):
            r, cfg = self.build(mods={"modBundled": ("Bundled", "bundle")}, unbundler=unbundler,
                                extra_args=["--target", "Bundled"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual("unbundling " in r.stdout, unbundler == "wcc_lite", r.stdout)
            hashes[unbundler] = content_hash(os.path.join(cfg["dist"], "Bundled"))
        self.assertEqual(hashes["builtin"], hashes["wcc_lite"])

    def test_unknown_unbundler_is_refused(self):
        r, _ = self.build(unbundler="magic", extra_args=["--dry-run"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown unbundler 'magic'", r.stderr)

    @unittest.skipUnless(have_ffdec(library=True), NO_FFDEC)
    def test_as3_diff_written(self):
        r, cfg = self.build(as3diff=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(cfg["as3diff"], "modSmoothMap.as3.diff"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("+++ B/com/gskinner/motion/GTween.as", text)
        self.assertIn("_smTicker = new Timer(1);", text)

    def test_dry_run_runs_nothing(self):
        r, cfg = self.build(extra_args=["--dry-run"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("$ ", r.stdout)
        self.assertFalse(os.path.exists(cfg["dist"]))

    def assertCaught(self, mode, message):
        r, _ = self.build(mode)
        self.assertNotEqual(r.returncode, 0, f"{mode} was not caught")
        self.assertIn(message, r.stdout + r.stderr)

    def test_failing_tool_is_reported(self):
        self.assertCaught("fail", "cook failed (exit 3)")

    def test_cooker_leaving_source_swf_is_caught(self):
        self.assertCaught("cook_keeps_swf", "not a cooked resource")

    def test_cooker_changing_bytecode_is_caught(self):
        self.assertCaught("cook_alters_code", "bytecode differs")

    def test_corrupted_bundle_entry_is_caught(self):
        self.assertCaught("pack_wrong_file", "bundled file differs from the cooked file")

    def test_missing_metadata_store_is_caught(self):
        self.assertCaught("no_metadata", "no metadata.store")


if __name__ == "__main__":
    unittest.main()
