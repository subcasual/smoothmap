# Smooth Map

A mod for **The Witcher 3: Wild Hunt Remastered** (5.01) that makes the world map feel right at any
frame rate:

* **Smooth panning and zoom.** Gamepad and keyboard panning and zoom follow your frame rate instead
  of the game's fixed timers, at exactly vanilla speed.
* **Smooth animations.** Most of the map animations (slide-in, zoom between world and region, snapping 
  to a point of interest, fades) update every frame instead of 24 times a second.
* **Fast, clean opening.** Map icons are built as the menu slides in, in a manner that scales to your
  hardware and minimizes stuttering. The map fades in as soon as it's ready. Everything is in place
  from the first frame and appears smoothly with no flickering.

No gameplay changes. Safe to install and uninstall during a playthrough.

**Looking to use the mod? Get it from [Nexus Mods](https://www.nexusmods.com/witcher3/mods/13530).**

This repository holds the mod's source and the patching framework it's built with:

* [**The mod**](#the-mod): what Smooth Map changes, and how to build it.
* [**The patching framework**](#the-patching-framework): tools for patching the ActionScript in
  Flash UI files, for this file or any other, in The Witcher 3 or another game.

## The mod

The game's map UI is a Flash file, `panel_worldmap.redswf`. Smooth Map doesn't ship edited source
code: it's a **patch set**, [`patches/smoothmap.yaml`](patches/smoothmap.yaml), a list of small
changes to the file's ActionScript bytecode. Each one says why it exists and what it means in
ActionScript. Its settings (fade duration, pin budget, the slide-in...) are at the top.
[`docs/NOTES.md`](docs/NOTES.md) has the technical background: the game's runtime and file formats,
tooling, compatibility with other mods, what was tried, and the history.

### Building it

You need:

* **REDkit**, with its depot built (its `r4data` folder). The mod is built from the depot's copy of
  the map file, and packaged with REDkit's `wcc_lite`.
* **Python 3.13** with PyYAML and lz4: `pip install pyyaml lz4`.

Check that `redkit.bin` and `redkit.depot` in [`build.yaml`](build.yaml) point at your REDkit
install (the defaults are Steam's location), then:

    python tools/build_mod.py              # dist/modSmoothMap/ and dist/modSmoothMap.zip
    python tools/build_mod.py --dry-run    # just patch, and show the wcc_lite commands

The build patches the map file, then cooks and packs it with `wcc_lite`, checking each step's
result. To change a setting, put it in `build.yaml`'s `set:` (e.g. `{ fade: 0.3 }`).

### Compatibility versions

Other map mods ship their own copy of the map file, which would replace Smooth Map's. For those, the
build makes a compatibility version: Smooth Map applied to that mod's file. Put the other mod in
`mods/` (its folder as installed, e.g. `mods/modmapredraw/`), and the build also makes
`mod000SmoothMap_<ModName>`, to install next to it. Patches that don't fit the other mod's file are
left out, and the build lists them. A compatibility version contains the other mod's file, so ask
its author before publishing one.

## The patching framework

The engine behind Smooth Map isn't specific to the map, or to The Witcher 3. It patches the
ActionScript bytecode of Flash and Scaleform (GFX) files directly, with no decompiling and
recompiling, and CDPR's `.redswf` files, which hold The Witcher 3's UI.

* **Patches are anchored on code**, not on byte offsets: each one finds the instructions it
  changes by pattern, and checks every value it replaces. So a patch set written for the game's file
  also applies to another mod's version of it, wherever that mod left those spots alone.
* **It fails loudly.** If an anchor isn't found exactly where expected, nothing is written, and the
  error names the spot.
* **Patch sets stack**: several can be applied to one file, in order.
* **Every edited method is verified** (stack and scope) before the file is written.

### Writing patches

* [`docs/writing-patches.md`](docs/writing-patches.md) walks through it: finding the code, writing
  a patch, checking and applying it.
* [`swf_patch/patch-format.md`](swf_patch/patch-format.md) is the format's full reference.
* Or write the change in ActionScript: [`patch_generator`](patch_generator/README.md) turns edits
  of decompiled classes into a patch set, verified (needs [FFDec](https://github.com/jindrapetrik/jpexs-decompiler)
  and Java).

### Using it

    python -m redswf_patch apply -p my-patches.yaml IN.redswf OUT.redswf
    python -m redswf_patch check -p my-patches.yaml FILE.redswf     # which patches apply to a file
    python -m redswf_patch lint my-patches.yaml
    python -m swf_patch ...                                         # the same, for plain SWF/GFX files

Python 3.9+ and PyYAML. The tools in `tools/` help with inspecting files: disassembling a method,
comparing two files, decompiled diffs, getting a file out of a mod. They take `vanilla` to mean
REDkit's depot copy of the map (`redkit.depot` in `build.yaml`):

    python tools/disasm.py vanilla HubMap handleScrollAndZoomTimer
    python tools/diff.py vanilla build/panel_worldmap.redswf

## Repository

| Path | What it is |
|---|---|
| `patches/smoothmap.yaml` | The mod. |
| `patches/diagnostics.yaml` | A testing overlay: an on-screen timeline of the map's opening. Not for release. |
| `build.yaml`, `tools/build_mod.py` | Building the mod and its compatibility versions. |
| `docs/` | `NOTES.md` (technical background and history), and the walkthrough for writing patches. |
| `swf_patch/` | The patching engine for SWF/GFX files, and the patch format's reference. |
| `redswf_patch/` | `swf_patch` for `.redswf` files. |
| `patch_generator/`, `tools/generate_patch.py` | Patch sets from edited ActionScript. |
| `ffdec_utils/` | Finding and running FFDec (for the patch generator and decompiled diffs). |
| `redbundle/`, `lz4block/` | Reading files out of mods' bundles. |
| `tools/disasm.py`, `diff.py`, `as3diff.py`, `extract.py` | Inspecting files: bytecode, differences, decompiled diffs, files out of mods. |
| `tests/` | `python -m unittest -v`. Most tests need REDkit's depot. Use Python 3.13: on Windows, 3.14+ compresses differently, and the byte-exact tests fail. |

The game's files and other authors' mods are never included here.

## AI usage

Both the mod and the patching framework were made with AI (Claude), to different degrees.

**The mod:** I specified the behavior that should change; the majority of code was written by AI 
with minor tweaks by me. I understand, have reviewed, and stand by each line of ActionScript.
I verify the bytecode edits by decompiling them into ActionScript. I test every build in game.

**The patching framework:** AI built the engine, the patch format, the tools, and the docs to my
specifications. I set its direction and key design decisions, and reviewed it incrementally as it
was built. I understand the framework at a high level but do not claim to understand or review
every line of code. Changes to the framework are verified by running the test suite and checking the
output, including the decompiled ActionScript, identical hashes, and behavior in game.

## License

The code in this repository is licensed as described in [LICENSE](LICENSE).

The Witcher 3 and its files belong to CD PROJEKT RED; patch sets refer to their code only by the
names and short instruction sequences needed to find the spots they change.
