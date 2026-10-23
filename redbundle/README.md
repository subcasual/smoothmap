# redbundle

Reading files out of REDengine 3 bundles (`blob0.bundle` etc., as found in The Witcher 3 mods),
and finding a game file in a mod: in its bundles, or as a loose file. Read-only: building bundles
is left to CDPR's tools (REDkit's `wcc_lite`).

Requires Python 3.9+ and [`lz4block`](../lz4block/README.md). With the `lz4` package from PyPI
installed (`pip install lz4`), LZ4-compressed entries are read about 100x faster.

```python
from redbundle import find_file

# a mod folder (searched recursively), one .bundle, or the file itself; slashes either way
data, where = find_file("mods/modSomething", "gameplay/gui_new/swf/worldmap/panel_worldmap.redswf")
```

`find_file` raises `LookupError` if the file isn't there or there's more than one copy;
`find_files` returns every copy.

## Swappable pieces

* **The unbundler.** `find_file(source, depot_path, unbundle)` reads bundles through any function
  `(bundle path, depot path) -> bytes or None` (None: not in this bundle). The default,
  `bundle_reader()`, is this package's reader; a caller can pass one that runs other tools, e.g.
  `wcc_lite unbundle` (Smooth Map's `tools/build_mod.py` does).
* **The LZ4 decompressor.** `bundle_reader(lz4)` and `Bundle(data, lz4)` take one of
  `lz4block`'s decompressors, e.g. `lz4block.decompressor("builtin")`. The default is python-lz4 when
  installed, else the built-in one.

## Modules

| Module | What it does |
|---|---|
| `bundles.py` | The bundle format: header, file table (both known entry layouts), stored, zlib and LZ4 entries, CRC check. |
| `mods.py` | Finding a file in a mod folder, a bundle, or a loose file; the `Unbundler` interface. |
