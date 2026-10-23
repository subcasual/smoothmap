"""REDkit's wcc_lite, as the build uses it: running the command lines configured in build.yaml, and
extracting files from bundles with it (an Unbundler for redbundle).

wcc_lite reads its configuration from REDkit's bin folder, so it runs from there. Each run takes
about 15 s, mostly startup.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess


class WccError(Exception):
    """wcc_lite couldn't run, failed, or didn't produce what it should have."""


class Wcc:
    """Runs configured wcc_lite commands, logging each run to <log_dir>/<command>.log.

    commands: name -> argument list, with {wcc} (wcc_lite.exe) and the places given to run() filled
    in. bin_dir: REDkit's bin folder (None: wcc_lite.exe from PATH). dry_run: only print commands."""

    def __init__(self, commands: dict[str, list[str]], bin_dir: str | None, dry_run: bool, log_dir: str) -> None:
        self.commands, self.bin, self.dry_run, self.log_dir = commands, bin_dir, dry_run, log_dir

    def run(self, name: str, **places: str) -> None:
        template = self.commands.get(name)
        if not template:
            raise WccError(f"build.yaml has no command for '{name}'")
        wcc = os.path.join(self.bin, "wcc_lite.exe") if self.bin else "wcc_lite.exe"
        argv = [str(arg).format(wcc=wcc, **places) for arg in template]
        print(f"    $ {' '.join(shlex.quote(a) for a in argv)}")
        if self.dry_run:
            return
        cwd = self.bin if self.bin and os.path.isdir(self.bin) else None
        try:
            result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace")
        except OSError as e:
            raise WccError(f"{name}: couldn't start {argv[0]} ({e}); check redkit.bin in build.yaml")
        log = os.path.join(self.log_dir, f"{name}.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write(f"$ {' '.join(argv)}\n\n{result.stdout}\n{result.stderr}")
        if result.returncode != 0:
            tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-15:])
            raise WccError(f"{name} failed (exit {result.returncode}); full output in {log}\n{tail}")


class WccUnbundler:
    """An Unbundler (see redbundle.mods) that has wcc_lite extract the files. `wcc_lite unbundle`
    extracts every file of every bundle in a folder, so each bundle is copied to a folder of its own
    under `work`, extracted once (about 15 s), and its files are kept for later lookups. Runs even on
    a dry run: it only reads inputs."""

    def __init__(self, commands: dict[str, list[str]], bin_dir: str | None, work: str) -> None:
        self.commands, self.bin, self.work = commands, bin_dir, work
        shutil.rmtree(work, ignore_errors=True)
        self.extracted: dict[str, str] = {}           # bundle path -> folder of its files

    def __call__(self, bundle_path: str, depot_path: str) -> bytes | None:
        key = os.path.normcase(os.path.abspath(bundle_path))
        if key not in self.extracted:
            self.extracted[key] = self._extract(bundle_path, os.path.join(self.work, str(len(self.extracted))))
        path = os.path.join(self.extracted[key], *depot_path.split("\\"))
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def _extract(self, bundle_path: str, folder: str) -> str:
        bundles, files = os.path.join(folder, "bundles"), os.path.join(folder, "files")
        os.makedirs(bundles)
        os.makedirs(files)
        shutil.copy(bundle_path, bundles)
        print(f"unbundling {bundle_path}:")
        Wcc(self.commands, self.bin, dry_run=False, log_dir=folder).run("unbundle", bundles=bundles, unbundled=files)
        if not any(names for _, _, names in os.walk(files)):
            raise WccError(f"unbundle: wcc_lite extracted nothing from {bundle_path}")
        return files
