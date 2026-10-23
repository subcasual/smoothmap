"""Finding a game file in a mod: inside its bundles, or as a loose file.

A file is named by its depot path, e.g. gameplay\\gui_new\\swf\\worldmap\\panel_worldmap.redswf.
The source can be a mod folder (searched recursively), a .bundle, or the file itself.

Files are read out of bundles by an Unbundler: any function (bundle path, depot path) -> the file's
bytes, or None if the bundle doesn't contain it. The default is bundle_reader(), this package's own
reader (bundles.py); callers can pass another, e.g. one that runs CDPR's wcc_lite.
"""
from __future__ import annotations

import glob
import mmap
import ntpath
import os
from typing import Callable, Optional

import lz4block
from .bundles import Bundle

Unbundler = Callable[[str, str], Optional[bytes]]


def bundle_reader(lz4: lz4block.Decompressor = lz4block.DEFAULT) -> Unbundler:
    """An Unbundler using bundles.Bundle, decompressing LZ4 entries with `lz4`."""
    def unbundle(bundle_path: str, depot_path: str) -> Optional[bytes]:
        # Memory-mapped, so only the table and the file we want are read from disk.
        with open(bundle_path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            bundle = Bundle(data, lz4)
            entry = bundle.find(depot_path)
            return bundle.read(entry) if entry else None
    return unbundle


def find_files(source: str, depot_path: str, unbundle: Optional[Unbundler] = None) -> list[tuple[bytes, str]]:
    """Every copy of the file in `source`, as (bytes, where it was found)."""
    unbundle = unbundle or bundle_reader()
    filename = ntpath.basename(depot_path)
    if os.path.isfile(source) and not source.lower().endswith(".bundle"):
        with open(source, "rb") as f:
            return [(f.read(), source)]
    if os.path.isfile(source):
        bundles = [source]
    else:
        bundles = sorted(glob.glob(os.path.join(source, "**", "*.bundle"), recursive=True))
    found = []
    for path in bundles:
        if not os.path.getsize(path):
            continue
        data = unbundle(path, depot_path)
        if data is not None:
            found.append((data, f"{path} :: {depot_path}"))
    if os.path.isdir(source):
        for path in sorted(glob.glob(os.path.join(source, "**", filename), recursive=True)):
            with open(path, "rb") as f:
                found.append((f.read(), path))
    return found


def find_file(source: str, depot_path: str, unbundle: Optional[Unbundler] = None) -> tuple[bytes, str]:
    """The one copy of the file in `source`, as (bytes, where it was found). Raises LookupError if
    there's none or more than one."""
    found = find_files(source, depot_path, unbundle)
    filename = ntpath.basename(depot_path)
    if not found:
        raise LookupError(f"no {filename} found in {source}")
    if len(found) > 1:
        raise LookupError(f"more than one {filename} in {source}:\n  " + "\n  ".join(where for _, where in found))
    return found[0]
