"""Build installable mods: patch -> cook -> pack -> metadata store -> mod folder -> zip.

    python tools/build_mod.py                      # every target + a compatibility variant per mod in compat.mods
    python tools/build_mod.py --target release     # one target (compatibility variants are named by {name})
    python tools/build_mod.py --dry-run            # patch and stage, print the wcc_lite commands
    python tools/build_mod.py --config other.yaml --version 19
    python tools/build_mod.py --hash <mod folder>  # content hash of an installed copy, to compare

Each stage's output is verified before the next one runs:
  cook  -> the result is a cooked resource whose bytecode is exactly what we patched
  pack  -> reading the bundle back gives the cooked file, byte for byte (CRC checked)
  meta  -> metadata.store exists
Already-cooked inputs (e.g. another mod's file, for a compatibility variant) skip the cook step.
Files are read out of bundles as build.yaml's `unbundler` and `lz4` options say; the pack check
always uses redbundle's own reader, so it doesn't rely on the tool it checks.
wcc_lite itself is run by tools/wcc.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import re
import shutil
import sys
import zipfile
from typing import Any

import yaml

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, ROOT)
sys.path.insert(0, TOOLS)
import lz4block                                        # noqa: E402
import redswf_patch                                    # noqa: E402
from redbundle import Bundle, Unbundler, bundle_reader, find_file, find_files   # noqa: E402
from redswf_patch import RedSwf                        # noqa: E402
from swf_patch.swf import read_abc                     # noqa: E402
from vanilla import VANILLA, vanilla_path              # noqa: E402
from wcc import Wcc, WccError, WccUnbundler            # noqa: E402

VORTEX_MARKER = "__folder_managed_by_vortex"   # Vortex's file in deployed mod folders; not part of the mod


class BuildError(Exception):
    pass


# --------------------------------------------------------------------------- content hash

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(folder: str) -> str:
    """SHA-256 of a manifest of every file under folder ("<sha256>  <relative/path>" lines, sorted by
    path): it depends only on file names and contents, so identical files give the same hash on every
    build, unlike the zip's own hash, which changes with each file's timestamp."""
    entries = []
    for directory, _, files in os.walk(folder):
        for name in files:
            if name == VORTEX_MARKER:
                continue
            path = os.path.join(directory, name)
            with open(path, "rb") as f:
                entries.append((os.path.relpath(path, folder).replace(os.sep, "/"), f.read()))
    return manifest_hash(entries)


def manifest_hash(entries: list[tuple[str, bytes]]) -> str:
    """content_hash's algorithm over (relative/path, bytes) pairs, e.g. a zip's entries."""
    return sha256("".join(f"{sha256(data)}  {rel}\n" for rel, data in sorted(entries)).encode())


# --------------------------------------------------------------------------- configuration

def resolve(base: str, path: str) -> str:
    """A path from build.yaml: relative to the file's folder unless absolute."""
    return path if os.path.isabs(path) or path[1:3] == ":\\" else os.path.normpath(os.path.join(base, path))


def native(depot_path: str) -> str:
    """A depot path (backslashes) as a relative path on this system."""
    return os.path.join(*depot_path.split("\\"))


def read_info(mod: str) -> dict[str, Any]:
    """The mod's info.json (REDkit writes it in content/), or {}."""
    for directory, _, files in os.walk(mod):
        if "info.json" in files:
            try:
                with open(os.path.join(directory, "info.json"), encoding="utf-8-sig") as f:
                    return json.load(f)
            except (OSError, ValueError):
                return {}
    return {}


def redkit_bin(cfg: dict, base: str) -> str | None:
    """REDkit's bin folder (redkit.bin), where wcc_lite.exe is."""
    bin_dir = (cfg.get("redkit") or {}).get("bin")
    return resolve(base, bin_dir) if bin_dir else None


def readers(cfg: dict, base: str) -> tuple[Unbundler, lz4block.Decompressor]:
    """How files are read out of bundles: build.yaml's `unbundler` (builtin or wcc_lite) and `lz4`
    (python-lz4 or builtin). Raises ValueError for an unknown choice or a missing lz4 package."""
    lz4 = lz4block.decompressor(cfg.get("lz4", "python-lz4"))
    choice = cfg.get("unbundler", "builtin")
    if choice == "builtin":
        return bundle_reader(lz4), lz4
    if choice == "wcc_lite":
        work = resolve(base, os.path.join(cfg.get("work", "build/package"), "_unbundled"))
        return WccUnbundler(cfg.get("commands") or {}, redkit_bin(cfg, base), work), lz4
    raise ValueError(f"unknown unbundler '{choice}' (choose builtin or wcc_lite)")


def compat_targets(cfg: dict, base: str, unbundle: Unbundler | None = None) -> tuple[dict[str, dict], list[str]]:
    """One target per mod in compat.mods that contains the resource, named by its {name}; and the
    mods skipped. {name} is the mod's info.json modName without spaces or punctuation, else its
    folder name without the conventional "mod" prefix (modOtherMap -> OtherMap).
    unbundle: how bundles are read (default: redbundle's reader)."""
    compat = cfg.get("compat")
    if not compat:
        return {}, []
    folder = resolve(base, compat["mods"])
    if not os.path.isdir(folder):
        return {}, [f"{folder} (no such folder)"]
    targets, skipped = {}, []
    for entry in sorted(os.listdir(folder)):
        mod = os.path.join(folder, entry)
        if not os.path.isdir(mod):
            continue
        if not find_files(mod, cfg["resource"], unbundle):
            skipped.append(entry)
            continue
        info = read_info(mod)
        display = str(info.get("modName") or re.sub(r"(?i)^mod(?=.)", "", entry))
        name = re.sub(r"[^A-Za-z0-9]", "", display) or entry
        if name in targets:
            raise BuildError(f"two mods in {folder} are both named {name}")
        values = dict(name=name, mod=display, mod_version=info.get("version", ""), version="{version}")
        targets[name] = dict(
            input=mod, patches=compat.get("patches"), set=compat.get("set"), profile=compat.get("profile"),
            skip_failing=compat.get("skip_failing", False),
            mod_folder=compat["mod_folder"].format(**values), zip=compat["zip"].format(**values),
            info_overrides={k: str(v).format(**values).strip() for k, v in (compat.get("info_json") or {}).items()})
    return targets, skipped


def files_named(folder: str, name: str) -> list[str]:
    return [os.path.join(d, f) for d, _, files in os.walk(folder) for f in files if f.lower() == name.lower()]


# --------------------------------------------------------------------------- the stages

def patch(target: dict, cfg: dict, base: str, unbundle: Unbundler) -> tuple[bytes, bytes, list[tuple[str, str]]]:
    """(input file, patched file, patches left out). Every patch must apply, unless the target says
    skip_failing: then those that don't are left out (each one whole) and reported, but at least
    one must apply."""
    source = vanilla_path(cfg) if target["input"] == VANILLA else resolve(base, target["input"])
    data, where = find_file(source, cfg["resource"], unbundle)
    patch_sets = [resolve(base, p) for p in target.get("patches") or []]
    problems = redswf_patch.lint(patch_sets)
    if problems:
        raise BuildError("patch set problems:\n  " + "\n  ".join(problems))
    left_out: list[tuple[str, str]] = []
    if target.get("skip_failing"):
        patched, report = redswf_patch.apply_fitting(data, patch_sets, target.get("profile"), target.get("set") or {})
        left_out = redswf_patch.failed(report)
        if not any(status == "ok" for _, status in report):
            raise BuildError("no patch applies to this file")
    else:
        patched = redswf_patch.apply(data, patch_sets, target.get("profile"), target.get("set") or {})
    print(f"  patched {where} ({'cooked' if RedSwf(patched).cooked else 'depot'} resource)")
    for patch_id, reason in left_out:
        print(f"  LEFT OUT {patch_id}: {reason}")
    return data, patched, left_out


def cook(patched: bytes, resource: str, wcc: Wcc, places: dict[str, str]) -> str:
    """The cooked file's path. Depot files are cooked by wcc_lite from a REDkit-style project
    (project/workspace/<resource>, which overrides REDkit's own copy); cooked ones are used as they are."""
    cooked_file = os.path.join(places["cooked"], native(resource))
    if RedSwf(patched).cooked:
        os.makedirs(os.path.dirname(cooked_file), exist_ok=True)
        with open(cooked_file, "wb") as f:
            f.write(patched)
        print("  cook: skipped (input already cooked)")
        return cooked_file

    staged = os.path.join(places["workspace"], native(resource))
    os.makedirs(os.path.dirname(staged), exist_ok=True)
    with open(staged, "wb") as f:
        f.write(patched)
    print("  cook:")
    wcc.run("cook", **places)
    if wcc.dry_run:
        return cooked_file
    if not os.path.exists(cooked_file):
        found = files_named(places["cooked"], ntpath.basename(resource))
        if len(found) != 1:
            raise BuildError(f"cook: expected {native(resource)} under {places['cooked']}, found {found or 'nothing'}")
        cooked_file = found[0]
    with open(cooked_file, "rb") as f:
        result = RedSwf(f.read())
    if not result.cooked:
        raise BuildError("cook: the output still contains the source SWF (not a cooked resource)")
    if read_abc(result.gfx) != read_abc(RedSwf(patched).gfx):
        raise BuildError("cook: the cooked file's bytecode differs from what we patched")
    print("    verified: cooked resource, bytecode identical to the patch output")
    return cooked_file


def pack(cooked_file: str, resource: str, wcc: Wcc, places: dict[str, str], lz4: lz4block.Decompressor) -> None:
    print("  pack:")
    wcc.run("pack", **places)
    if wcc.dry_run:
        return
    content = places["content"]
    bundles = [p for p in (os.path.join(d, f) for d, _, fs in os.walk(content) for f in fs)
               if p.lower().endswith(".bundle")]
    if len(bundles) != 1:
        raise BuildError(f"pack: expected one .bundle in {content}, found {bundles or 'none'}")
    try:
        with open(bundles[0], "rb") as f:
            bundle = Bundle(f.read(), lz4)
    except ValueError as e:
        raise BuildError(f"pack: {bundles[0]} is not a readable bundle ({e})")
    entry = bundle.find(resource)
    if not entry:
        raise BuildError(f"pack: {resource} is not in the bundle (entries: {[e.name for e in bundle.entries]})")
    try:
        bundled = bundle.read(entry)
    except ValueError as e:
        raise BuildError(f"pack: can't read {resource} back from the bundle ({e})")
    with open(cooked_file, "rb") as f:
        if bundled != f.read():
            raise BuildError("pack: the bundled file differs from the cooked file")
    others = [e.name for e in bundle.entries if e is not entry]
    print(f"    verified: bundle contains {resource}, byte-identical to the cooked file"
          + (f"; also contains {others}" if others else ""))


def write_metadata(wcc: Wcc, places: dict[str, str]) -> None:
    print("  metadatastore:")
    wcc.run("metadatastore", **places)
    if not files_named(places["content"], "metadata.store"):
        raise BuildError(f"metadatastore: no metadata.store in {places['content']}")
    print("    verified: metadata.store written")


def package(name: str, target: dict, cfg: dict, base: str, content: str, version_override: str | None) -> str:
    """dist/<target>/<mod folder>/content (+ info.json) and its zip. Returns the zip's path."""
    dist = resolve(base, cfg.get("dist", "dist"))
    shutil.rmtree(os.path.join(dist, name), ignore_errors=True)
    mod_dir = os.path.join(dist, name, target["mod_folder"])
    shutil.copytree(content, os.path.join(mod_dir, "content"))
    info = cfg.get("info_json")
    if info:
        info = dict(info, **(target.get("info_overrides") or {}))
        info["version"] = str(version_override or info.get("version", ""))
        with open(os.path.join(mod_dir, "content", "info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
    version = str(version_override or (info or {}).get("version", "dev"))
    zip_name = target.get("zip", f"{target['mod_folder']}_{{version}}.zip").format(version=version)
    zip_path = os.path.join(dist, zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for directory, _, files in os.walk(mod_dir):
            for file_name in sorted(files):
                path = os.path.join(directory, file_name)
                z.write(path, os.path.relpath(path, os.path.join(dist, name)))
    print(f"  mod folder: {mod_dir}")
    print(f"  zip:        {zip_path}")
    print(f"  content:    {content_hash(mod_dir)[:16]}  (same files, same hash: compare with a tested build)")
    return zip_path


def write_as3_diff(original: bytes, patched: bytes, target: dict, cfg: dict, base: str) -> None:
    """The ActionScript diff against the input (vanilla, or the other mod's file), for source control."""
    from as3diff import as3_diff                  # tools/as3diff.py
    out = os.path.join(resolve(base, cfg["as3diff"]), f"{target['mod_folder']}.as3.diff")
    try:
        text = as3_diff(original, patched, log=lambda message: None)
    except SystemExit as e:                       # FFDec/Java missing or failing: the mod itself is fine
        print(f"  as3 diff:   not written ({e})")
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    print(f"  as3 diff:   {out}")


def build_target(name: str, target: dict, cfg: dict, base: str, args: argparse.Namespace,
                 unbundle: Unbundler, lz4: lz4block.Decompressor) -> list[tuple[str, str]]:
    """Build one target; returns the patches left out of it (see patch)."""
    print(f"== {name}")
    resource = cfg["resource"]
    work = resolve(base, os.path.join(cfg.get("work", "build/package"), name))
    shutil.rmtree(work, ignore_errors=True)
    project = os.path.join(work, "project")
    places = dict(project=project, workspace=os.path.join(project, "workspace"),
                  cooked=os.path.join(work, "cooked"), content=os.path.join(work, "packed", "content"))
    for folder in (places["workspace"], places["cooked"], places["content"]):
        os.makedirs(folder)
    wcc = Wcc(cfg.get("commands") or {}, redkit_bin(cfg, base), args.dry_run, log_dir=work)

    original, patched, left_out = patch(target, cfg, base, unbundle)
    cooked_file = cook(patched, resource, wcc, places)
    pack(cooked_file, resource, wcc, places, lz4)
    if args.dry_run:
        print("  (dry run: stopping before verification and packaging)")
        return left_out
    write_metadata(wcc, places)
    package(name, target, cfg, base, places["content"], args.version)
    if cfg.get("as3diff"):
        write_as3_diff(original, patched, target, cfg, base)
    return left_out


# --------------------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=os.path.join(ROOT, "build.yaml"))
    parser.add_argument("--target", action="append", help="build only these targets")
    parser.add_argument("--version", help="override info_json.version (also used in zip names)")
    parser.add_argument("--dry-run", action="store_true", help="patch and stage, print commands, run nothing")
    parser.add_argument("--hash", metavar="MOD_FOLDER", help="only print the content hash of a mod folder "
                        "(the one containing content/), e.g. an installed copy, and exit")
    args = parser.parse_args(argv)
    if args.hash:
        if not os.path.isdir(os.path.join(args.hash, "content")):
            raise SystemExit(f"{args.hash} has no content/ folder; pass the mod folder itself")
        print(f"content: {content_hash(args.hash)[:16]}  {args.hash}")
        return

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not cfg.get("resource"):
        raise SystemExit(f"{args.config}: no 'resource' (the depot path of the file to patch)")
    base = os.path.dirname(os.path.abspath(args.config))
    try:
        unbundle, lz4 = readers(cfg, base)
    except ValueError as e:
        raise SystemExit(f"{args.config}: {e}")
    targets = dict(cfg.get("targets") or {})
    try:
        variants, skipped = compat_targets(cfg, base, unbundle)
    except (BuildError, WccError) as e:
        raise SystemExit(f"\nBUILD FAILED: {e}")
    clash = [name for name in variants if name in targets]
    if clash:
        raise SystemExit(f"compatibility variant(s) {clash} have the same name as a target in build.yaml")
    targets.update(variants)
    if skipped:
        print(f"compat: no {ntpath.basename(cfg['resource'])}, skipped: {', '.join(skipped)}")

    chosen = args.target or list(targets)
    unknown = [name for name in chosen if name not in targets]
    if unknown:
        raise SystemExit(f"unknown target(s) {unknown}; available: {list(targets)}")
    failed, partial = [], {}
    for name in chosen:
        try:
            left_out = build_target(name, targets[name], cfg, base, args, unbundle, lz4)
            if left_out:
                partial[name] = [patch_id for patch_id, _ in left_out]
        except (BuildError, WccError, redswf_patch.PatchError, LookupError, FileNotFoundError) as e:
            print(f"  FAILED: {e}")
            failed.append(name)
    if len(chosen) > 1:
        print(f"\n{len(chosen) - len(failed)} of {len(chosen)} built" + (f"; failed: {', '.join(failed)}" if failed else ""))
    if partial:
        print("\nBuilt WITHOUT some patches (they don't fit that mod's file; test these in game):")
        for name, patch_ids in partial.items():
            print(f"  {name}: {', '.join(patch_ids)}")
    if failed:
        raise SystemExit(f"\nBUILD FAILED: {', '.join(failed)}")


if __name__ == "__main__":
    main()
