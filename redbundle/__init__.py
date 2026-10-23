"""redbundle: reading REDengine 3 bundles (blob0.bundle etc.) and finding a game file in a mod.

    from redbundle import find_file
    data, where = find_file("mods/modSomething", "gameplay/gui_new/swf/worldmap/panel_worldmap.redswf")

README.md in this folder has the details. Requires Python 3.9+ and lz4block (in this repository);
the lz4 package from PyPI is optional and makes LZ4 entries about 100x faster to read.
"""
from .bundles import Bundle, BundleEntry
from .mods import Unbundler, bundle_reader, find_file, find_files

__all__ = ["Bundle", "BundleEntry", "Unbundler", "bundle_reader", "find_file", "find_files"]
