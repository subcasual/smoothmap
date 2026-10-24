# patch_generator

Write a patch set by editing ActionScript instead of bytecode: export the classes you want to
change as decompiled ActionScript, edit them in place, and generate the patch set from your edits.
The result is an ordinary [`swf_patch`](../swf_patch/README.md) patch set: readable, lintable, and
applied without any of the tools below.

Requires Python 3.9+, `swf_patch` and [`ffdec_utils`](../ffdec_utils/README.md) (in this repository;
PyYAML), and [JPEXS FFDec](https://github.com/jindrapetrik/jpexs-decompiler) with Java. FFDec is only needed to
write patches, not to apply them.

## Workflow

With Smooth Map's tool (it accepts a `.redswf`, a `.swf`/`.gfx`, or a mod folder):

```
python tools/generate_patch.py export vanilla HubMap      # REDkit's depot copy -> build/edit/.../HubMap.as
(edit build/edit/red/game/witcher3/menus/worldmap/HubMap.as)
python tools/generate_patch.py build -o patches/my-change.yaml
python -m redswf_patch apply -p patches/my-change.yaml "<depot>\gameplay\gui_new\swf\worldmap\panel_worldmap.redswf" out.redswf
```

As a library, on a SWF or GFX file's bytes:

```python
import patch_generator

patch_generator.export(gfx, ["HubMap"], "work")         # work/<package path>/HubMap.as, to edit
result = patch_generator.generate(gfx, "work")          # result.yaml, result.methods
```

Then fill in each patch's `why`. The generator fills in `as3`: the lines you changed, with a little
context (the whole method when it's replaced whole).

## How it works

1. **Find what you changed.** FFDec compiles both the exported source and your edited source, each
   with the methods you didn't edit stubbed out (so FFDec only compiles what you edited; line numbers
   in its errors still match your file). Comparing the two compiles, rather than your file with the
   original bytecode, cancels out FFDec's own way of compiling, so exactly the methods you edited
   show up as changed. Changed line endings or trailing spaces don't count as edits.
2. **Write the patch.** New fields and constants become `add_field` (with their initial value),
   new methods, getters and setters (static or not, with their parameters) `add_method`, and
   changed, added or removed initial values (`var x:Number = 60`) `set_initial_values`, placed
   first so edited methods can use them. Names your code uses that the file doesn't have yet
   (a class it never mentioned) are declared first with `add_names`. Each changed method becomes
   an `edit` changing only what you changed (see below), or where that isn't possible yet, one
   whose `replace` step matches the method's entire original code and replaces it with FFDec's
   compile of your version. New local variables become the edit's `locals:`, named after your
   source's variables (a third, probe compile shows which register each variable got: FFDec has no
   simple rule for it), so a decompiled view of the patched file shows your names; FFDec's own
   temporaries (loop iterators) are `localN`.
3. **Verify.** Before returning, the generator lints the patch set and applies it to the file. Each
   method changed in place must come out as exactly the original with your changes made; each
   method replaced whole as exactly FFDec's compile; every other method unchanged.

**Changing only what you changed** (`align.py`). The differences between the two FFDec compiles are
your changes, as instructions inserted, replaced or deleted. FFDec's code is lined up with the
original's the way patterns compare code (names by name, `initproperty` and `setproperty` alike,
numbers by value), and each change is placed where the instructions around it line up. Each step
is anchored on the original's own instructions around the change, with as little context as
matches exactly one place, like a hand-written patch. The result touches only what you changed,
so it combines with other patch sets that change the same method elsewhere (Smooth Map, say).

New code may branch (`if`, loops, `switch`): its jumps within itself get labels of their own
(`target1:`...). A jump to a place in the original code (a changed condition still jumping to the
original `else`) gets a landing label, put there by a step of its own: patch code can't name the
original's labels, but it can put a new label at the same place.

A method is replaced whole instead (its patch title says so) when a change can't be placed yet:
when the original's code around it doesn't line up with FFDec's, when the original jumps into the
changed code or the change removes one of its labels (removing an `if` block, say), or when it
reuses a local variable whose register can't be matched between the compiles. A whole-method patch
applies only where that method is exactly as exported, and stops with a message anywhere else.

## Limits (so far)

* Some changes still replace the whole method (see above), so such a patch can't combine with
  another that changes the same method. When the only change to a class initializer is the value
  of constants or static fields (`KEYBOARD_SCROLL_SPEED:int = 25`), the patch is a
  `set_initial_values`.
* A patch that only inserts code still matches after it's been applied, so applying it to the same
  file twice inserts the code twice (as with a hand-written insert). Patches that add members are
  refused the second time, by `add_field` and `add_method`.
* Your code is checked only as far as FFDec's compiler checks it, which is less than Adobe's
  strict mode: a misspelled name compiles (as a run-time lookup) and fails in game
  (`ReferenceError #1065`). Test generated patches in game like any other.
* The generator also refuses code FFDec compiled wrongly in a way that would fail at run time: a
  `setproperty` on a constant (constants are assigned with `initproperty`).
* Not supported yet: `try`/`catch`, nested functions, changing a method's parameters or return
  type, methods using `arguments` or `...rest`, names private to a file. Removing or renaming
  members isn't supported either. The generator lists every such problem at once.
* FFDec's compiler must accept its own decompilation of the methods you edit (and the class's
  declarations). It fails on a few constructs: in The Witcher 3's map file, `MapMenu`'s constructor
  (as of FFDec 26.3.0), so editing that one method fails with a message saying so; every other
  method of the class can be edited. `NaN` parameter defaults, which FFDec also rejects, are
  handled: defaults belong to the method's signature, which patches never change.
* A patch applies wherever the original code around each change is still there: one generated from
  vanilla also applies on top of another patch set (or another mod's file) that changed other
  parts of the same methods. A method replaced whole needs that method exactly as exported, so for
  those, export from the file the patch will apply to.

For a change the generator can't make, write that part by hand, as a patch set of its own or added
to the generated one: [`docs/writing-patches.md`](../docs/writing-patches.md#by-hand) walks through it.

## Modules

| Module | What it does |
|---|---|
| `workflow.py` | `export` and `generate`: compiling the exported and edited source, comparing them class by class. |
| `versions.py` | The three versions of an edited class compared: the original file's, and FFDec's compiles of the exported and the edited source. |
| `members.py` | New fields and methods (`add_field`, `add_method`), changed initial values (`set_initial_values`). |
| `methods.py` | Changed methods: anchored where `align.py` can place the change, else replaced whole; names for new locals. |
| `align.py` | Placing changes in the original method: lining up the compiles, anchoring each change. |
| `checks.py` | Why a change can't be generated (yet): the limits above. |
| `reading.py` | Facts read from bytecode: members, types, initial values, visibility. |
| `patches.py` | The kinds of patch generated, as data. |
| `writing.py` | Writing the patches out as a readable patch set. |
| `verify.py` | Applying the patch set and checking it makes exactly the intended changes. |
| `source.py` | ActionScript text: class file paths, finding methods, preparing source for FFDec (stubbing unedited methods). |
