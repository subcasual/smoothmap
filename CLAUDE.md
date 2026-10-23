# Smooth Map (The Witcher 3 Remastered UI mod)

Patches CDPR's world map UI (`gameplay\gui_new\swf\worldmap\panel_worldmap.redswf`, game version
5.01) for framerate-independent panning and zoom, smooth map animations, and fast, flicker-free map
opening. The mod is a **declarative YAML patch set** applied by **`redswf_patch`**: the `.redswf`
container over **`swf_patch`**, a generic engine that edits the ActionScript bytecode in SWF/GFX
files directly (no decompile/recompile).

Read first: `patches/smoothmap.yaml` (each change's `why` and `as3`), `docs/NOTES.md` (runtime facts,
file formats, toolchain traps, compatibility findings, what was tried and rejected, history),
`swf_patch/patch-format.md` (the patch format spec), `swf_patch/README.md` (the engine's modules and
flow). `docs/writing-patches.md` walks through writing a patch.

## Layout

| Path | What it is |
|---|---|
| `patches/smoothmap.yaml` | **The mod.** Every change with `why` and its ActionScript equivalent (`as3`). Tunable parameters at the top. |
| `patches/diagnostics.yaml` | Testing overlay (on-screen opening timeline; `--set slowmo=true` stretches the fade). Never ship it. |
| `swf_patch/` | The engine: patch sets applied to the bytecode of SWF/GFX files; the format spec; a CLI (Python 3.9+, PyYAML). See its README. |
| `redswf_patch/` | `swf_patch` for `.redswf` files: the CR2W container, every payload patched identically; the CLI used here. |
| `patch_generator/`, `tools/generate_patch.py` | Patch sets from edited ActionScript: export classes (decompiled with FFDec), edit them, build; each change is patched in place, anchored on the original code around it (or the method replaced whole where it can't be), verified. See its README for the limits. |
| `ffdec_utils/` | Finding and running FFDec: its command line (decompile, compile) or its library through the Java helper in `ffdec_utils/java` (decompile). Used by `patch_generator` and `tools/as3diff.py`. |
| `redbundle/`, `lz4block/` | Reading REDengine 3 bundles and finding a game file in a mod (the default unbundler); the pure-Python LZ4 block decoder it falls back on when the `lz4` package isn't installed. Needed only to read mods' bundles. |
| `build.yaml`, `tools/build_mod.py`, `tools/wcc.py` | Packaging (`wcc.py`: running `wcc_lite`, and unbundling with it): patch -> `wcc_lite` cook/pack/metadatastore -> `mods/<folder>/content/` (+ `info.json`) -> zip, every stage verified. Also builds a compatibility variant for each mod in `mods/`. |
| `changes/` | `<mod folder>.as3.diff`: decompiled ActionScript diff of each built mod against its input, rewritten by every build (needs FFDec). Contains decompiled CDPR code, so it's a separate private git repo, ignored by this one: `git -C changes diff` before committing a version. |
| `tools/disasm.py`, `tools/diff.py`, `tools/as3diff.py` | Inspect methods (output syntax = pattern syntax); bytecode diff of two files; ActionScript diff via FFDec (decompile only). |
| `tools/extract.py` | A game file out of a mod's bundles (`--swf`: the Flash file inside, for FFDec). |
| `tests/` | `test_swf_patch.py` (engine, patch sets, verifier), `test_redswf_patch.py` (container; Smooth Map on the real file; release hash), `test_redbundle.py`, `test_packages.py` (package dependency rules), `test_patch_generator.py` (needs FFDec), `test_tools.py`, `test_build.py` (against `fake_wcc_lite.py`). |
| `mods/`, `build/`, `dist/` | Other authors' mods, outputs (gitignored). |

## Commands

Use Python 3.13 (stock zlib): Python 3.14+ on Windows ships zlib-ng, which compresses differently,
so the byte-hash tests fail there. Setup once: `py install 3.13`, `py -V:3.13 -m venv .venv`,
`.venv\Scripts\python.exe -m pip install pyyaml lz4`; then activate `.venv` or call its python directly.

```
python -m redswf_patch lint patches/smoothmap.yaml patches/diagnostics.yaml
python -m redswf_patch apply -p patches/smoothmap.yaml "<depot>\gameplay\gui_new\swf\worldmap\panel_worldmap.redswf" build/panel_worldmap.redswf
python -m redswf_patch apply -p patches/smoothmap.yaml -p patches/diagnostics.yaml IN OUT    # testing overlay
python -m redswf_patch check -p patches/smoothmap.yaml <any panel_worldmap.redswf>          # per-patch report
python -m redswf_patch explain -p patches/smoothmap.yaml                                    # changelog text
python tools/build_mod.py [--dry-run] [--target release] [--version 1.1]                    # installable mods + zips
python tools/build_mod.py --hash <mod folder>        # content hash of any copy (dist, Vortex staging, game mods/)
python tools/disasm.py vanilla HubMap <method>        # tools: vanilla = the depot copy; or any .redswf
python tools/diff.py vanilla build/panel_worldmap.redswf
python tools/generate_patch.py export vanilla HubMap   # then edit build/edit/..., then:
python tools/generate_patch.py build -o patches/my-change.yaml
python -m unittest -v
```

"Vanilla" is REDkit's **depot** copy of the map, at `build.yaml`'s `redkit.depot` (REDkit's `r4data`):
the release builds from it (target input `vanilla`), the tools take `vanilla` for it, and tests
need it to be 5.01's (sha256 `628d81120c14fca9abd5e0ab21a793e88c1fa56e8244171e2fd6b6e041b66ba5`), skipping otherwise.
Packaging needs REDkit's `wcc_lite` (path in `build.yaml`, `redkit.bin`).

## Rules

- **Edit the YAML, not code**, for any mod change. After every change: lint, then the full test suite.
- **Lint is not optional.** Valid YAML isn't enough: an outdented label silently becomes a stray key and
  truncates a code block; an unquoted comma splits an inline-map string.
- **The release hash** in `tests/test_redswf_patch.py` (`EXPECTED_RELEASE`) changes only on purpose, and is
  marked "pending" until the maintainer confirms the build in game.
- **Refactors of the engine or tools must not change output.** Compare the patched bytes (and the
  build's content hashes) before and after; the release-hash test is the minimum.
- **Patches are anchor-based and fail loudly**: every pattern matches exactly once (unless `count`),
  constants are checked against their expected value, nothing is addressed by byte offset, failures
  raise `PatchError` and write nothing. All copies of the bytecode (GFX, and the source SWF in depot
  files) are patched identically.
- **One behaviour per patch, self-contained.** Compatibility variants leave out each patch that doesn't
  fit the other mod's file (whole, and reported), so no patch may depend on another, structurally or in
  behaviour: e.g. raising vanilla's pins-per-tick cap belongs in the patch with the time budget that
  replaces it. Every patch must still apply when any other is left out.
- **Smallest change that works.** A small footprint keeps the merge risk low with other map mods (mainly
  ones that recompile `MapMenu`). Ask before adding gameplay changes or new scope.
- **Never ship a file FFDec recompiled.** Its recompiler broke `HubMap.CenterOnPin`'s `NaN` parameter
  defaults (crash on map load). FFDec is for reading (`tools/as3diff.py`), and `patch_generator` uses
  its compiler only to get the bytecode of edited methods, which goes into CDPR's file as a verified
  patch; nothing FFDec writes is shipped.
- **The packages stay generic and layered**: nothing specific to one game file, and each imports only
  what `tests/test_packages.py` allows: `redswf_patch` uses `swf_patch`, `patch_generator` uses `swf_patch` and `ffdec_utils`, `redbundle` uses `lz4block`,
  nothing else crosses packages, and nothing imports `tools/`. `swf_patch` knows nothing of `.redswf`.
- **Python 3.9 is the floor for the engine and tools**: no 3.10+ syntax at run time (`match`, `X | Y`
  outside annotations). Annotations may use modern syntax with `from __future__ import annotations`.
- **Verify claims against the bytecode** (`tools/disasm.py`, decompiled diffs) before stating how
  vanilla behaves; several earlier assumptions turned out wrong once checked.
- **Never commit CDPR assets or other authors' mods.** A compatibility variant redistributes the other
  mod's file: publish one only with its author's permission.
- **Never publish to Nexus or run `wcc_lite` concurrently** without the maintainer's explicit go-ahead.
- Run `wcc_lite` from REDkit's bin folder (`build_mod.py` does). Even with REDkit's EULA accepted, its
  dialog reappears on every run unless `HKCU\Software\CD Projekt RED\Mod Tools\1.0\DoNotShowEula` is
  the *string* "1".
