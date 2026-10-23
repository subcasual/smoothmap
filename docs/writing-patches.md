# Writing patches

This walks through writing a patch set for a REDengine Flash file, from finding the code to
building a mod. The example makes map panning faster; the same steps work for any `.redswf` the
game uses. The full reference is [`patch-format.md`](../swf_patch/patch-format.md). For a plain SWF
or GFX file (another game's UI, say), use `python -m swf_patch` where this says `python -m
redswf_patch`; everything else is the same.

You need Python (3.13 recommended) with PyYAML, and the file you want to change:

* **The game's own file**: the uncooked ("depot") version, from REDkit's depot (`r4data`), e.g.
  `<depot>\gameplay\gui_new\swf\worldmap\panel_worldmap.redswf`. With `redkit.depot` set in
  `build.yaml`, the tools take `vanilla` for the map's.
* **Another mod's version**: `python tools/extract.py <mod folder> -o build/other.redswf` pulls it
  out of the mod's bundles. Use `--resource <depot path>` for a file other than the map.

## 1. Understand the code

Read the ActionScript first. [JPEXS FFDec](https://github.com/jindrapetrik/jpexs-decompiler)
decompiles Flash files, but a `.redswf` is a REDengine container around one:
`python tools/extract.py <file or mod> --swf` writes the Flash file inside, for FFDec to open. Don't
save edits from FFDec: its recompiler is known to break this file. (To write a change as
ActionScript, use the patch generator below, which turns your edits into a verified patch instead.)

Then look at the bytecode you'll anchor on. `tools/disasm.py` prints it in the patch format's own
syntax, so you can copy patterns straight from it:

    python tools/disasm.py vanilla --classes worldmap   # find a class
    python tools/disasm.py vanilla HubMap               # its members
    python tools/disasm.py vanilla HubMap handleScrollAndZoomTimer
    python tools/disasm.py vanilla HubMap "<static-init>"   # static constants

For the example, two spots matter. The keyboard speed is a static constant, set in `HubMap`'s
static initializer:

    findproperty    KEYBOARD_SCROLL_SPEED
    pushbyte        15
    initproperty    KEYBOARD_SCROLL_SPEED

and the stick's position is scaled by 0.5 in `handleScrollAndZoomTimer`, once per axis:

    getlocal0
    getproperty     _gamepadScrollX
    pushdouble      0.5
    multiply
    getlocal0
    getproperty     _gamepadScrollY
    pushdouble      0.5
    multiply
    callproperty    scrollMap, 2

## 2. Write the patch set

`faster-panning.yaml`:

```yaml
format: 1

parameters:                           # values users can tune (--set name=value)
  keyboard_speed: { default: 25,  doc: "Keyboard panning speed (vanilla 15)" }
  stick_factor:   { default: 0.8, doc: "Gamepad panning multiplier (vanilla 0.5)" }

patches:
  - id: faster-keyboard-panning
    title: Keyboard panning is faster
    why: |
      Vanilla pans the regional map by 15 units per update with WASD.
    as3: |
      KEYBOARD_SCROLL_SPEED = 25;   // HubMap; was 15
    ops:
      - set_initial_values:           # refuses unless the constant is still 15
          class: HubMap
          values:
            KEYBOARD_SCROLL_SPEED: { expect: 15, value: "{{keyboard_speed}}" }

  - id: faster-stick-panning
    title: Gamepad panning is faster
    why: |
      handleScrollAndZoomTimer scales the stick's position by 0.5 before scrolling.
    as3: |
      this.scrollMap(this._gamepadScrollX * 0.8, this._gamepadScrollY * 0.8);   // was 0.5
    ops:
      - edit:
          method: HubMap.handleScrollAndZoomTimer
          steps:
            - replace:
                after: |              # context before (not replaced)
                  getproperty     *
                match: |              # what's replaced
                  pushdouble      0.5
                before: |             # context after (not replaced)
                  multiply
                count: 2              # one per axis; without count, exactly one match is required
              with: push {{stick_factor}}
```

`title`, `why` and `as3` are documentation: they explain the patch to the next reader, and
`python -m redswf_patch explain` turns them into a changelog. `push` emits whichever push
instruction fits the number.

## 3. Check and apply

    python -m redswf_patch lint faster-panning.yaml
    python -m redswf_patch apply -p faster-panning.yaml "<depot>\gameplay\gui_new\swf\worldmap\panel_worldmap.redswf" build/panel_worldmap.redswf
    python -m redswf_patch apply -p faster-panning.yaml --set stick_factor=1 IN OUT   # another value

`apply` lints first, applies every patch, verifies every edited method (stack and scope depth),
and writes nothing if anything fails. Then compare:

    python tools/diff.py vanilla build/panel_worldmap.redswf      # bytecode
    python tools/as3diff.py vanilla build/panel_worldmap.redswf   # ActionScript (FFDec)

`check` reports, patch by patch, whether a patch set fits a file, which is how you see whether it
works on another mod's version:

    python -m redswf_patch check -p faster-panning.yaml build/other.redswf

## 4. Build and test in game

Either put the patched depot file in a REDkit project at the same path and use Save and Publish,
or let `tools/build_mod.py` do the whole chain (patch, cook, pack, metadata store, mod folder, zip):
copy `build.yaml`, change `targets` to your patch set and mod folder, and run
`python tools/build_mod.py --config my_build.yaml`. It needs REDkit's `wcc_lite`.

## Or: write the change in ActionScript

Instead of steps 1 and 2, you can edit the decompiled ActionScript and let
[`patch_generator`](../patch_generator/README.md) write the patch set (needs FFDec and Java):

    python tools/generate_patch.py export vanilla HubMap
    (edit build/edit/red/game/witcher3/menus/worldmap/HubMap.as)
    python tools/generate_patch.py build -o faster-panning.yaml

The generated patch changes only what you changed, anchored on the original instructions around
each change, and is verified before it's written; fill in its `why` and continue with step 3.
Changing `KEYBOARD_SCROLL_SPEED` to 25 in the source gives exactly the `set_initial_values` above.
A few kinds of change (removing an `if` block, say) still replace the whole method, which keeps
them from combining with other patch sets that change the same method; the generator's README
lists those and what else it can't do yet.

## Combining patch sets

Several patch sets apply in order, sharing one set of parameters and snippets (names must not
collide), e.g. your patches on top of Smooth Map:

    python -m redswf_patch apply -p patches/smoothmap.yaml -p faster-panning.yaml IN OUT

Patterns match whatever code is there by then, so a later set sees the earlier one's changes.

## Which operation

| You want to | Use |
|---|---|
| change a constant's or field's initial value | `set_initial_values` |
| add a field to a class | `add_field` |
| add a method, getter or setter | `add_method` |
| use a name the file doesn't contain yet (a class it never mentions) | `add_names` |
| insert code at a method's start, before its returns, or at a pattern | `edit` with `insert` |
| replace or delete instructions | `edit` with `replace` (`with: ""` deletes) |
| refuse files where some code exists (or is missing) | `guard` |
| reuse an operand exactly as the file wrote it (e.g. a runtime name) | `capture`, then `$name` |
| repeat a block of code in several places | a snippet |

## Common mistakes

* **A pattern matches zero or several times.** Add `after:`/`before:` context until it matches
  exactly once, or say how many with `count: N` / `count: all`.
* **Alternatives are for instructions, not operands.** `initproperty|setproperty x` works;
  `getproperty a|b` looks for a name called `a|b`. Use `*` for "any operand", or two steps.
* **Steps see the original method.** Every step of an `edit` matches against the method as it was
  before that edit, not after the previous step.
* **Private members are written `Class.member`.** A plain name must be a class or an existing
  public name; `getproperty _snapTween2` fails, `getproperty HubMap._snapTween2` works.
* **Numbers out of range.** `pushbyte` takes -128..127 and `pushshort` 16 bits; `push N` picks the
  right one, so use it for parameters.
* **YAML eats your code.** Keep every line of a code block at one indentation: a label outdented to
  the level of `code:` silently becomes a YAML key and cuts the block short. Quote values starting
  with `{` (`value: "{{fade}}"`), and strings with commas or colons inside `{ ... }`. `lint` catches
  these.
* **Locals must be declared.** `locals: [dt]` on the edit; register instructions take a declared
  name or a number.
* **Other mods' recompiled files differ in details.** A mod rebuilt with another compiler may write
  `setproperty` where CDPR's compiler wrote `initproperty`, drop redundant conversions, or rename
  locals. Anchor on what matters (names, constants) and allow the rest (`initproperty|setproperty`,
  `*`). `set_initial_values` already accepts both.
* **Jump targets.** Labels in `disasm.py` output (`L0:`) are only for reading; in patterns, use
  `<jump-target>` for "an instruction a jump lands on".
