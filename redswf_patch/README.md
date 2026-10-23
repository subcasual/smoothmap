# redswf_patch

[`swf_patch`](../swf_patch/README.md) for CDPR's `.redswf` files: the REDengine resources that
hold The Witcher 3's Flash UI. Same patch sets, same commands; this package adds the container.

```
.redswf (REDengine CR2W resource)
  └─ CSwfResource chunk, ending in two buffers:
       1. GFX payload  (what the game runs)
       2. source SWF   (what REDkit re-imports from); empty in cooked files, i.e. those inside
                       published mods' bundles
         └─ the ABC bytecode, the same in both
```

`apply` patches every copy of the bytecode identically (the same bytecode twice is patched once),
then rebuilds the file, recomputing everything REDengine checks: chunk sizes and offsets, CRC32s,
file sizes, the header CRC. Rebuilding with unchanged payloads reproduces the input byte for byte.

Requires Python 3.9+ and `swf_patch` (in this repository; PyYAML). To get a `.redswf` out of a
mod's bundles first, see [`redbundle`](../redbundle/README.md).

## Command line and library

```
python -m redswf_patch apply   -p A.yaml [-p B.yaml ...] [--profile P] [--set name=value ...] IN.redswf OUT.redswf
python -m redswf_patch check   -p A.yaml [...] IN.redswf
python -m redswf_patch explain -p A.yaml [...]
python -m redswf_patch lint    A.yaml [B.yaml ...]
```

```python
import redswf_patch

with open("panel_worldmap.redswf", "rb") as f:
    data = f.read()
out = redswf_patch.apply(data, ["my_patches.yaml"], overrides={"fade": 0.3})
report = redswf_patch.check(data, ["my_patches.yaml"])
out, report = redswf_patch.apply_fitting(data, ["my_patches.yaml"])   # leaves out patches that don't apply
abc = redswf_patch.load_abc("panel_worldmap.redswf")      # swf_patch.abcfile.ABC, for inspection
```

The commands, patch format and errors (`PatchError`) are `swf_patch`'s.

| Module | What it is |
|---|---|
| `redswf.py` | `RedSwf`, the CR2W container: reading its payloads, rebuilding it; `load_abc`. |
| `engine.py` | `apply`, `apply_fitting` and `check` over the file's payloads, using `swf_patch`'s `patch_abc`, `patch_abc_fitting` and `check_abc`. |
| `__main__.py` | `swf_patch`'s command line, with this package's `apply` and `check`. |
