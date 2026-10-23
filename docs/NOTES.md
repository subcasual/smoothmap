# Smooth Map: technical notes

What isn't written down anywhere else: facts about the game's runtime and file formats, tooling
traps, compatibility findings, what was tried and rejected, and a short history. What each change
does and why is in [`patches/smoothmap.yaml`](../patches/smoothmap.yaml) (`why` and `as3` of each
patch). Game version: The Witcher 3 Remastered 5.01 (PC).

## Runtime facts (Scaleform in REDengine)

* The SWF frame rate is 24: `ENTER_FRAME` and timeline animations run at 24 Hz.
* AS3 `Timer`s are serviced every engine advance (once per rendered frame), independent of the SWF
  frame rate; that's why `Timer(1)` gives per-frame updates. Measured: one tick per frame, no
  catch-up (a 1 ms timer would otherwise fire ~250 times in 254 ms).
* GTween completes a zero-length tween synchronously inside `GTweener.to()`, running its completion
  handlers before the calling code expects: use 0.001 s, not 0.
* Classes compiled into this file (including its own copy of `CoreMenu`) are the ones that run; other
  menus keep theirs.
* Gamepad stick values arrive at least once per frame (the consume-and-zero pattern works at 120 Hz).
* Each menu's `InputManager` starts as gamepad (`_isGamepad = true`); scripts pass only
  `isGamepad` (`setControllerType`), and "mouse" is detected from mouse events. The game delivers one
  as the menu opens, so the switch arrives ~47 ms in even without touching the mouse.
* A key held before the map opens doesn't pan it (same as vanilla): `HubMap` starts keyboard scrolling
  on the press event, which happened before the menu existed. A held stick works (sent continuously).
* A plain `TextField` renders boxes: the game has no system fonts. Text must use Scaleform's mapped
  fonts (`$NormalFont`, `$BoldFont`) with `embedFonts = true`.
* `BlendMode.LAYER` renders semi-transparent content visibly thinner than `NORMAL`.
* `MapMenu`'s root timeline depth 1 is an opaque black shape (1924x855) under everything in the menu.
  It isn't part of the map's fade: it comes with the menu (with the slide's alpha), covering the root
  menu's background panorama, and the map fades in on top. The root background (panel_common) never
  hides in 5.01: nothing registers `panel.main.background.shows`.

## The map's opening sequence (vanilla 5.01)

`Enable` -> `StartInitialTimer` (20 ms) -> waits for `_menuAnimCompleted` (set by
`MapMenu.handleShowAnimComplete` after `CoreMenu.showAnimation`: y 500 -> 0 and alpha 0 -> 1 over 0.8 s,
`Exponential.easeOut`, which looks finished at ~0.4-0.5 s) -> `processMapPins` (30 per tick) ->
`finalizeMapPinsProcessing` (centers the map) -> `StartShowTimer` (`SHOW_INTERVAL` 200 ms) ->
`handleShowTimer`: fades (gradient and pins 0.33 s, tiles 0.66 s) -> `handleFadingInEnded` (side
panels, input). `OnMenuShown`, sent one frame after the slide, only hides the root menu background.
`UpdateVisibileArea` is called only from `UpdateVisibilityAndPinPositions` (map moved, zoomed, mouse
moved, pins changed, loading done). Other facts: `KEYBOARD_SCROLL_SPEED = 15`; `scrollMap` buffers
movement while a pin is selected (`SNAP_FREE_LIMIT = 16`); `UpdateSelectedMapPin` snaps via
`CenterOnPin` unless using a mouse; `updatePins` (pins changing on an open map) builds every pin at
once with `processMapPins(true)`.

Not in the patch set's documentation:
* Stick and keyboard zoom now call `ZoomByFactor` every frame while zooming, and it repositions every
  pin on each call: a cost vanilla paid only every 17 ms.
* The side panels' fade (`side-panels-with-map`) anchors only on method starts and returns, so it
  also fits `MapMenu`s other mods recompiled. If quest data isn't loaded at the reveal, the tracker
  shows at fade end, as in vanilla.
* With `slide: false`, the two slide-only patches drop out: 17 changed methods instead of 18, and no
  added method.

## File formats

**Depot .redswf (CR2W v164).** Header 160 bytes: magic, version, flags, timestamp, build, file size
@24, buffer size @28, CRC @32, chunk count; then 10 tables (offset, count, crc32). Table 4 lists the
chunks (24 bytes each: class, flags, parent, size, offset, template, crc32 of the data). Header CRC =
CRC32 of bytes 0..160 with the CRC field set to 0xDEADBEEF; table and chunk CRCs are plain CRC32.
Chunk 0 is `CSwfResource`: properties (linkageName, textures, header{frameRate=24,...},
imageImportOptions; 5.01 dropped `importFile`), a terminator, then two u32-length-prefixed buffers:
the GFX payload (`CFX`, zlib) and the source SWF (`CWS`, zlib). Chunks 1-5 are `CSwfTexture`s (DDS).
**Cooked .redswf** (in the game's and mods' bundles): the same, with the source-SWF buffer empty.

**GFX vs SWF.** The ABC bytecode is identical in both payloads. The GFX adds Scaleform tags (1000+).
Its header length field doesn't equal the real length (CDPR's exporter), so it's adjusted by the
size difference, never recomputed.

**Bundles** (`blob0.bundle`): `POTATO70`, then a table of 0x130-byte entries (REDkit, e.g. Map
Remastered) or 0x140-byte ones (other packers, e.g. Smooth Menus); compression stored, zlib, or LZ4
block (types 4/5). Details in `redbundle/bundles.py`. The game's own large bundles use other layouts
the reader doesn't handle (`r4gui.bundle` reads fine). REDkit's `wcc_lite` writes bundles and
`metadata.store`; the tools only read them.

**ABC.** 5.00c's file was a debug build (debug names, `debugline`/`debugfile`, trace logging); 5.01's
is a release build (bytecode 1.24 MB -> 0.68 MB), so vanilla decompiles with FFDec's `_locN_`/`paramN`.
Quirks a lossless round trip must keep: some negative ints in the pool have non-minimal encodings
(the original bytes are kept); a few dead `jump`s target non-instruction offsets (their raw offset is
kept). Every level (container, payload, ABC, each method body) round-trips byte for byte; the tests
check it.

**Debug names.** Locals are numbered registers; names exist only as `debug 1, <name>, <register-1>,
<line>` instructions. Every local a patch adds gets one (decompilers show `dt`, not `_loc4_`). Once a
method has any debug names, FFDec shows unnamed registers blank (`function f(:Boolean)`), so the
engine also names unnamed parameters `paramN` when it adds names; a test enforces it.

**Exception handlers** address code by byte offset, and the engine doesn't remap them, so it refuses
to edit a method with `try/catch`. A `catch` also makes the compiler keep the method's locals in an
activation object (`getscopeobject`/`getslot`) instead of registers.

## Toolchain

**Patching** (`python -m redswf_patch apply`) takes ~0.6 s on the depot file, ~0.5 s on a cooked
one; about half of it is `guard` decoding every method once. Leaving out patches that don't fit
(compatibility versions) tries each patch on a copy: ~2.7 s.

**Packaging** (`tools/build_mod.py`): patch -> `wcc_lite` cook -> pack -> metadatastore -> mod folder
(+ `info.json`, which the build writes with REDkit's values) -> zip. Every stage is verified: the
cooked output must be a cooked resource with exactly the patched bytecode; the bundle, read back,
must contain the cooked file byte for byte (CRC checked); `metadata.store` must exist. Cooked inputs
(other mods' files) skip cook.
* `wcc_lite cook` loads each file by its depot path through REDkit's split depot (r4data plus a
  project workspace on top); `-mod=` only lists the files. So the build stages the patched file in a
  REDkit-style project (`<proj>\workspace\<depot path>`) and runs
  `cook -platform=pc -mod=<proj>\workspace -workspaceDir <proj>\ -outdir=<cooked>`, with
  `-workspaceDir` and the project root as **two arguments**: `-workspaceDir=<proj>\` is silently
  ignored and r4data's vanilla copy gets cooked (the bytecode check catches it). `-basedir=` crashes
  ("Remap is not supported for SplitEditorDepot!"). Cooking the depot file gives the game's own
  cooked file byte for byte.
* `wcc_lite` must run from REDkit's bin folder (it reads its configuration there). Even after you've
  accepted REDkit's EULA, `wcc_lite` shows the EULA dialog on every run unless
  `HKCU\Software\CD Projekt RED\Mod Tools\1.0\DoNotShowEula` is the *string* "1" (a DWORD is ignored). Each run overwrites REDkit's `bin\wcc.log`. Harmless log noise:
  CreateFile failures for .ini/.settings files, asserts about the depot path and sound banks, a port
  37000 bind failure while REDkit is open.
* Each run takes ~14-16 s, almost all engine startup. Runs aren't parallelized: no evidence
  `wcc_lite` supports it, and they share `wcc.log`.
* Reading other mods' bundles: `unbundler: builtin` (redbundle) or `wcc_lite` (`wcc_lite unbundle
  -dir=<folder with one bundle> -outdir=<absolute path>`, ~15 s per bundle, and it runs even on
  `--dry-run`). Both give the same bytes. `uncook -unbundleonly -targetfile=` would extract a single
  file, but exits 0 having extracted nothing. The check of what `pack` wrote always uses redbundle's
  reader, so it doesn't rely on the tool it checks.
* The build prints a **content hash** per mod (`--hash <folder>` for any copy): SHA-256 of a sorted
  `<sha256>  <path>` manifest of the mod folder, ignoring Vortex's `__folder_managed_by_vortex`. Zip
  hashes change with every build (timestamps); the content hash only when a file does. The dist folder,
  Vortex's staging folder and the deployed game folder give the same hash.
* REDkit's own reimport of the SWF doesn't work (importing a raw .swf crashes the editor). Not needed:
  the engine writes the depot file, REDkit only cooks and packs. To test through REDkit instead: put
  the patched file in a project's workspace at the same path, then Save and Publish.

**Python 3.13.** Python 3.14+ on Windows ships zlib-ng: same data, different compressed bytes, so the
byte-hash tests fail there although the content is identical. The engine and tools stay
3.9-compatible.

**Reviewing changes.** Every build writes `changes/<mod folder>.as3.diff`: the decompiled ActionScript
diff of the mod against its input (`tools/as3diff.py`; only the changed classes are decompiled, ~2.5 s).
It contains decompiled CDPR code, so `changes/` is its own private git repository, ignored by the
main one: `git -C changes diff` shows a version's changes in ActionScript. `tools/diff.py` is the exact
bytecode diff; it matches methods by name, so it also works on files another tool recompiled.

## Compatibility with other mods

A compatibility version is Smooth Map applied to another mod's `panel_worldmap.redswf` (cooked, from
its bundle) and packed alone. The other mod stays installed (for its scripts, other files and
minimap), standalone Smooth Map is removed, and the version loads first by name (`mod000...` sorts
before other mod folders; a mod whose folder sorts earlier still, e.g. `mod0000_...`, needs its
version named differently). It redistributes the other mod's file, so it needs that author's
permission. Two other mods that both change the map file can't be combined: that would mean merging
their changes, which the engine doesn't do.

Other mods often rebuilt the whole file with another compiler: reshuffled pools, every method
renumbered, `setproperty` where CDPR's compiler wrote `initproperty`, no redundant `convert_b`,
`getlex NaN` for NaN locals, FFDec's names. Patterns allow what doesn't matter
(`initproperty|setproperty`, `*`). Compare such files by name (`tools/diff.py`, `tools/as3diff.py`),
never by method index. A patch that doesn't fit is left out of the version, whole, and reported; so
each patch must be one self-contained behaviour.

* **Map Remastered** 2.4.2: vanilla 5.01 plus edits, recompiled (redrawn icons, quest levels;
  ~250 methods, none of ours). All patches apply. Tested in game; its author gave permission.
* **Gwent Players Map**: code only in `HubMapPinPanel` (a "Gwent" category, 4 pin types), plus 4
  icons added as frames to `PinIcon_122`. All patches apply. Published. It and Map Remastered both
  change `PinIcon_122`, so a version for both at once would mean merging their graphics.

## Testing

`python -m unittest -v` covers the engine and patch sets, the patch generator (with FFDec), the
tools, and packaging against a `wcc_lite` stand-in. The release hash test pins the exact output of
the build confirmed in game.

`patches/diagnostics.yaml` is an on-screen timeline of the opening (pins done, fade, slide done,
first input), applied on top of the mod; `--set slowmo=true` stretches the map fade to 3 s. Never
released.

In game, on the regional and world map:
* stick and WASD panning smooth and at vanilla speed, at native refresh and with the frame limiter at 30/60;
* stick/keyboard zoom smooth and at vanilla speed, also while panning; mouse wheel one step per notch;
* slide-in, world<->region zoom and snap-to-POI glide smooth;
* the map appears during the slide, sharp, with no hitch in dense regions (Velen/Novigrad, Skellige);
* initial centering on player/quest correct; panning possible from the start of the fade;
* edge arrows and crosshair fade in with the map (no crosshair for mouse users); legend and quest
  tracker appear at the start of the fade;
* snapping to a pin during the fade leaves pins opaque; a quick region switch and back doesn't flash
  the crosshair.

Lessons from earlier bugs, for testing changes:
* Accepting input earlier exposes code that assumed the fade had finished: look for `removeTweens`
  and direct alpha writes on the faded layers (`mcHubMapPinContainer`, `mcLodContainer`,
  `mcGradientContainer`).
* Anything computed from screen coordinates during the slide is offset by it.
* When the game updates, compare the patched methods by name: if they're identical, the patches
  carry over, whatever else changed in the file.

## Considered and not done

* **Recompiling with FFDec**: its recompiler broke `CenterOnPin`'s optional `NaN` defaults (crash on
  load), and its P-code assembler rejects `NaN`. Bytecode patching avoids both and keeps edits
  minimal. (`patch_generator` uses FFDec's compiler only to find what an edit changed; nothing FFDec
  compiles is shipped.)
* **Raising the SWF frame rate**: would also speed up timeline animations (~10 animated sprites:
  crosshair capture, pin glow, quest pointers) unless they were resampled. The GTween ticker reached
  the smoothness goal without it.
* **Insta-load**: no slide, no fades, all pins in one tick. Building every pin in one frame can hitch
  in dense regions. Its edge-arrow hold was kept.
* **Fading the black backing with the map**: cross-fading the root menu's landscape into the map
  looked uneven; the map needs a stable dark background to fade in on.
* **Waiting for the background tiles before the fade**: it never waited (tiles always loaded before
  the pins finished).
* **Restoring vanilla's local names** from 5.00c's debug info: possible where the register layout
  didn't change, but it would touch many more methods.
* **Hashing decompressed payloads** so the tests pass on zlib-ng Pythons: standardized on 3.13.
* **Gameplay changes** (the old 4.04 mod's POI snap, snap radius, buffering bypass): out of scope.

## History

| Build | Changes |
|---|---|
| 1-2 | Fast open on 5.01: pins built during the slide, every frame within a 3 ms budget; 0.5 s fade. |
| 3-7 | Tuning, from on-screen timelines: ~0.25 ms per pin, so a 6 ms budget (282 pins in +139 ms over 10 frames); fade 0.3 s, then back to 0.5 s once input no longer waited for it (build 6: input from the start of the fade). Background tile wait removed. |
| 8 | Fix: snapping to a pin during the fade left pins translucent (`CenterOnPin` removed the fade's tween). |
| 10 | Reverted a quest-preview alignment call from build 2: a misdiagnosis (the visible jump was the edge arrows). |
| 11-12 | Edge arrows, then the crosshair, fade in with the map (the crosshair flashed for mouse users). |
| 13 | Stick/keyboard zoom per frame; unnamed parameters named `paramN`. |
| 14-15 | Slide kept for consistency with other menus. Build 15: a full review of the decompiled diff. |
| 16 | No-slide design: the shell appears at once, the map layer fades in when loaded. |
| 18 | First build by the declarative engine (the Python patcher it replaced gave the same bytes, but for `maxstack` in five methods, now exact). Release values: fade 0.2 s, budget 8 ms, slide kept (no-slide is a parameter). Fix for part of the map staying blurry after opening: the visible area is recomputed at slide end. |
| 19-20 | Tiles for the final view requested during the slide, so the map is sharp from the start (the blur still lasted ~1 s in 18). Confirmed in game. |
| Oct 2026 | Engine and tools reviewed for readability, output byte-identical; patch generator; compatibility versions leave out patches that don't fit; open-sourced. |

The game's 5.01 update didn't change any method Smooth Map patched (5.00c and earlier were patched
before this history starts; the next-gen 4.04 mod came first).

## Open ideas

* JSON Schema for the patch format (editor validation, LLM structured output).
* A "pattern" mode for `tools/disasm.py`: print a spot with context, ready to paste as `after:`/`before:`.
* Guard the GTween ticker against catch-up fires (skip if < 1 ms since the last tick), if a frame time
  comparison against vanilla ever shows overhead.
* Editing methods with `try/catch` (remapping exception handlers), for other mods' files that add them.
* A raw-byte prefilter for `guard` (skip methods that can't match): ~0.25 s, not worth the complexity yet.
