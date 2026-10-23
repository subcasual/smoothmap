# Patch format

A patch set is a YAML file describing changes to the ActionScript 3 bytecode of one Flash file: a
SWF or GFX, or one wrapped in a game's container, like The Witcher 3's `.redswf`. The engine,
`swf_patch` (`redswf_patch` for `.redswf` files), reads it and applies it; there is no per-mod
Python code. The examples come from Smooth Map, the first patch set (`patches/smoothmap.yaml` in
this repository) and a complete example of everything below. For a step-by-step introduction, see
[`docs/writing-patches.md`](../docs/writing-patches.md).

## Goals

* **Self-describing.** Structure is expressed as YAML keys, not as markers or mini-languages
  inside text. Every patch states why it exists and what it means in ActionScript.
* **Safe by construction.** Every change is anchored on code that must exist exactly as expected;
  anything unexpected stops the patcher with a message naming what it couldn't find. Nothing is
  addressed by byte offset.
* **Complete without code.** All mutations are expressed with the operations below; there is no
  escape hatch into Python (deliberately, for now).
* **Deterministic.** The same patch set, parameters and input always produce the same bytes.

## File structure

```yaml
format: 1                              # format version (this document)
target:                                # what the patch set is written against
  resource: gameplay\gui_new\swf\worldmap\panel_worldmap.redswf
  game: The Witcher 3 Remastered 5.01  # informational; patterns decide whether patches apply

parameters:                            # tunable values, referenced as {{name}}
  fade: { default: 0.4, doc: "Map layer fade-in duration, seconds" }
  slide: { default: true, doc: "Keep the menu's slide-in" }

profiles:                              # named parameter sets
  release: {}
  no-slide: { slide: false, fade: 0.3 }

snippets:                              # reusable code templates, see "Snippets"
  fade_in: { params: [member, duration], code: "..." }

patches:                               # applied in order
  - id: ...
```

A profile is a set of parameter values; boolean parameters serve as flags. Parameters can also be
overridden on the command line (`--set fade=0.3`).

## Patches

```yaml
- id: keep-pin-fade-on-snap           # unique, kebab-case
  title: Snapping to a pin doesn't stop the pins' fade-in
  why: |                               # free text: the problem and the reasoning
    ...
  as3: |                               # the change expressed as ActionScript (documentation)
    ...
  when: not slide                      # optional condition over parameters
  ops: []                              # operations, applied in order
```

`when` is a boolean expression over parameters using `and`, `or`, `not` and parentheses; a patch
whose condition is false is skipped entirely. `title`, `why` and `as3` are documentation; tools
can render them into change logs and mod descriptions.

## Operations

Each entry in `ops` is a mapping with exactly one key.

### `set_initial_values`

Changes the initial values of a class's members, refusing if a current value isn't the expected
one (another mod may have retuned it). `set_constants` is another name for it.

```yaml
- set_initial_values:
    class: HubMap
    values:
      SHOW_INTERVAL: { expect: 200, value: "{{show_interval}}" }
      FADING_TEXTURES_DURATION: { expect: 0.66, value: "{{fade}}" }
      _gamepadZoomValue: { expect: 0, value: 60 }
      _inScroll: { value: true }       # no `expect`: it has no initial value yet
      _bufPosY: { expect: 0 }          # no `value`: its initial value is removed
```

Compilers keep an initial value in one of two places, and the patcher changes it where it is:

* **Assigned by the class initializer** (static constants, like the first two): the patcher finds
  the assignment, whichever of `initproperty`/`setproperty` the compiler used, and pushes the new
  number with the smallest instruction that holds it (see `push` below).
* **Stored with the member's declaration** (`var x:Number = 0`): the stored value is replaced,
  added or removed. It can be a number, `true`, `false`, `null` or a string; for a `Number`, `NaN`,
  `Infinity` and `-Infinity` are written as words, as in ActionScript.

An instance field whose initial value is computed in the constructor's code has neither; edit the
constructor instead.

### `add_names`

Adds names the file doesn't contain yet, so that code in later operations can use them. Code can
only use names that exist, so a misspelled name in code still fails instead of quietly creating a
name nothing defines; `add_names` is the deliberate way to introduce one.

```yaml
- add_names:
    - public::Namespace                # a top-level class or function
    - flash.geom::Rectangle            # a name in a package (the package's namespace is added if need be)
```

Each name is written qualified (`public::Name`, `package::Name`, `AS3::name`, `"uri"::name`). A name
the file already has is left as it is, so the operation is safe to repeat and to combine with other
patch sets.

### `add_field`

```yaml
- add_field:
    class: GTween
    name: _smTicker
    type: flash.utils::Timer           # a class name, qualified when not unique
    visibility: protected              # private | protected | internal | public
    static: true                       # default false
    const: true                        # default false: a constant (const) instead of a variable (var)
    value: 0                           # optional initial value: true, false, null, a number or a string
```

`type` is a class name, a parameterized type (`__AS3__.vec::Vector.<int>`), or `*` for an untyped
field. `value` is what ActionScript writes as `var x:int = 0`: a constant stored with the field, not
code (`NaN`, `Infinity` and `-Infinity` as words, for a `Number`). Refuses if a member with that
name exists (this is also how double-patching is detected). After this operation the field is an
ordinary member (`GTween._smTicker`) for later patches.

### `add_method`

Adds a method, getter or setter, static or not. Its body is verified before it is added.

```yaml
- add_method:
    class: LodContainer
    name: smIsBackgroundLoading
    visibility: private                # default public
    static: true                       # default false
    kind: method                       # method (the default), get or set
    params:                            # default none; each a type, or { name, type, default }
      - { name: dt, type: Number }
      - { name: limit, type: int, default: 3 }
    returns: Boolean                   # a class name, void, or * (the default; void for a setter)
    locals: [background]               # named locals, registers after the parameters
    code: |
      ...
```

Named parameters can be used like locals (`getlocal dt`); unnamed ones by register (1, 2...). Only
the last parameters can have defaults (true, false, null, a number or a string; `NaN` as a word for
a `Number`). A getter takes no parameters; a setter takes one and returns `void`, and can be added
next to an existing getter of the same name (or the other way round) to make a pair. In a static
method, register 0 is the class rather than an instance.

### `guard`

Stops the whole run if a condition about the file doesn't hold.

```yaml
- guard:
    forbid:                            # none of these patterns may appear...
      - pattern: pushstring "useFrames"
      - pattern: "getproperty|setproperty|initproperty useFrames"
    outside: GTween                    # ...in any method outside this class
    message: A frame-based tween would run faster with the extra ticker.
```

`require` (same shape) demands that a pattern exists.

### `capture`

Takes operands from a pattern in some method, for use in later code (`$name`). Useful to reuse
names exactly as vanilla wrote them.

```yaml
- capture:
    method: ImageContainer.UpdateTiles
    pattern: getproperty $element:<runtime-name>
    occurrence: first                  # first | only (default: only = exactly one match)
```

### `edit`

Edits one method, or several methods identically. Patterns in all steps are matched against the
method as it was **before this edit** (so steps don't disturb each other); the result is
re-encoded and verified once.

```yaml
- edit:
    method: HubMap.handleScrollAndZoomTimer
    locals: [dt, now]                  # new named locals: getlocal dt / setlocal now
    steps:
      - insert: { at: method-start }   # right after the method's `getlocal0; pushscope`
        code: ...
      - insert: { at: before-each-return }   # before each return of the original code
        code: ...
      - insert:
          after: |                     # instructions just before the insertion point
            getproperty  _gamepadScrollX
            pushdouble   *
            multiply
        code: ...
      - replace:
          after: |                     # optional context before
            findpropstrict  Timer
          match: |                     # exactly the instructions replaced
            pushbyte        17
          before: |                    # optional context after
            constructprop   Timer
        with: push 1
```

* **`insert`** goes at `at: method-start`, at `at: before-each-return`, or between an `after:`
  pattern and a `before:` pattern (either may be omitted; together they must locate one point).
* **`replace`** replaces the instructions matched by `match:`, which may be preceded and followed
  by `after:`/`before:` context. `with: ""` deletes them. Labels between replaced instructions
  are kept, so jumps into the span land on the replacement.
* `count: N` or `count: all` on a location applies the step at every match instead of requiring
  exactly one.
* Steps at the same position within one edit appear in the order listed.

To edit several methods identically, use `methods:` with each method's own substitutions:

```yaml
- edit:
    methods:
      MapMenu.enableCategoryPanel: { panel: mcHubMapPinPanel }
      MapMenu.enableQuestTracker:  { panel: mcHubMapQuestTracker }
    locals: [wasVisible]
    steps:
      - insert: { at: method-start }
        code: |
          getlocal0
          getproperty     {{panel}}
          ...
```

## Patterns

One instruction per line, matched against the method's code with debug instructions ignored:

```
getproperty  _gamepadScrollX       # operand compared by name (namespace ignored; untyped x, Class.x work too)
pushdouble   *                     # any operand
*                                  # any single instruction
initproperty|setproperty x         # alternatives (written out; nothing is implicit)
pushbyte     17                    # numbers compared by value
pushstring   "useFrames"
getlex       $gt:GTweener          # capture the operand as $gt (any operand: $gt)
getproperty  $e:<runtime-name>     # a name taken from the stack (vector/array element access)
<jump-target>                      # a position that jumps land on (no instruction)
```

A pattern must match **exactly once** in its method unless `count` says otherwise.

## Code

Code is either a block of assembly text, or a list whose items are assembly blocks or snippet uses:

```yaml
code:
  - snippet: measure_dt
    args: { last: HubMap._smLastScrollTime, cal: 17 }
  - |
    getlocal0
    getlocal        dt
    setproperty     HubMap._smZoomScale
```

Assembly is AVM2 instructions, one per line, `name:` for labels, `#` comments. All lines of a
block share one indentation (YAML ends a `|` block at a shallower line, which would turn a label
into a stray key; `python -m swf_patch lint` catches this). Smooth Map's `tools/disasm.py` prints
methods in this syntax: its output can be copied into patterns, and as code it assembles back to the
same instructions.

| Operand | Meaning |
|---|---|
| `HubMap._snapTween2` | An existing member (field or method), resolved through the class: correct namespace even for private and internal members. Includes members added by earlier patches. `package::Class.member` when the class's short name isn't unique. |
| `flash.utils::getTimer` | A name in a package. Must exist. `public::name` is the top-level public name (where `name` alone would mean a class). |
| `alpha`, `PinPointersManager` | A plain name: a class's short name (resolving to the class), or else a public name; it must already exist. |
| `AS3::push`, `"uri"::name` | A name in the AS3 namespace, or in any namespace by its URI. Must exist. |
| `untyped alpha` | `alpha` looked up at run time through the namespaces open in the class's code: what the compiler produces for untyped access, like `item.alpha` where `item` has no type. Uses the namespace set the class's methods already use most. |
| `<runtime-name>` | A name taken from the stack, looked up the same way (`a[i]`). |
| `Vector.<flash.geom::Point>` | A parameterized type. Its parts must exist, written as above. |
| `dt` after a register instruction | A named local (`locals:`). `getlocal`, `setlocal`, `kill`, `inclocal_i` and similar always take a register, so a name there can only be a declared local. Unnamed registers stay numbers (`getlocal 1`). |
| `$gt` | A captured operand. |
| `{{fade}}` | A parameter, method substitution or snippet argument (textual, before parsing). |
| `17`, `0.5`, `"text"` | Literals. Strings are double-quoted, with `\n`, `\r`, `\t`, `\"` and `\\` as escapes. `pushint` and `pushuint` take the value, not a pool index. |
| `L1, [L2, L3]` | `lookupswitch`: the default label, then the case labels. |

`push <number>` is a pseudo-instruction emitting `pushbyte`, `pushshort` or `pushdouble` as the
value requires, so parameters can't silently overflow. Slot and scope instructions (`getslot`,
`setslot`, `getglobalslot`, `setglobalslot`, `getscopeobject`) take their index, `hasnext2` two
registers. Not available in assembly: instructions that create classes, closures or catch scopes
(`newclass`, `newfunction`, `newcatch`, `newactivation`) and `pushnamespace`. Labels are scoped to one `edit` (shared by
all its steps, so one step can jump to a label another step inserts) or to one `add_method`, with
two exceptions that keep repeated code valid: labels defined inside a snippet are private to each
use of it, and when a step applies at several points (`at: before-each-return`, `count`), the
labels its code defines are private to each copy.

Methods are written `Class.method`; `Class.<constructor>` and `Class.<static-init>` (the class
initializer) are the special forms. `Class.get x` and `Class.set x` pick one of a getter/setter
pair, and `Class.static x` a static method (needed only when an instance member has the same
name), as the tools name methods. Classes are written by their short name when it is unique in
the file, otherwise as `package::Class`.

## Snippets

A snippet is a named assembly template with parameters:

```yaml
snippets:
  fade_in:
    params: [member, duration]
    code: |
      getlex          com.gskinner.motion::GTweener
      getlocal0
      getproperty     {{member}}
      push            {{duration}}
      ...
```

It's used as a code item, `{ snippet: fade_in, args: { member: mcHubMapCrosshair, duration: "{{fade}}" } }`,
with exactly its declared parameters as arguments. Local names refer to the using edit's locals.
Labels the snippet defines are private to each use, so a snippet can be used several times in one
edit; jumps in the snippet can only target its own labels.

## Locals and debug names

Every named local gets a debug name, so decompilers show `dt` instead of `_loc4_`. Because a
method with any debug names otherwise shows unnamed parameters as blank, the patcher also names
every unnamed parameter `paramN`, as decompilers do.

## Substitution order

Method substitutions (`methods:`) and snippet arguments first, then parameters.

## Order and determinism

* Patches run in file order; operations within a patch in order; steps within an edit in order.
* New constant-pool entries are appended in order of first use, so reordering patches changes the
  output bytes (but not behavior) and must be deliberate.
* When different patches insert at the same position (e.g. `at: method-start` of the same method),
  the later patch's code ends up **closest to the insertion point**: first after the method
  start, first after an `after:` pattern, last before a `before:` pattern.

## Safety

* Every edited or added method passes a stack and scope verifier before the output is written.
  `maxstack` is set to exactly what the edited code needs (never below the original).
* When a container holds several copies of the bytecode (a `.redswf` depot file has the GFX payload
  and the source SWF), every copy is patched identically.
* Any failure (missing pattern, unexpected constant, existing member, failed guard or
  verification) stops the run and writes nothing.

## Tooling

The engine is the Python package `swf_patch` (Python 3.9+, PyYAML), for SWF and GFX files;
`redswf_patch` has the same commands and functions for `.redswf` files:

```
python -m swf_patch apply   -p A.yaml [-p B.yaml ...] [--profile P] [--set name=value ...] IN OUT
python -m swf_patch check   -p A.yaml [...] IN     # dry run: per patch, applies / why not
python -m swf_patch explain -p A.yaml [...]        # title, why, as3 of the active patches
python -m swf_patch lint    A.yaml [B.yaml ...]    # structure, labels, snippets, locals, placeholders
```

Several `-p` patch sets are applied in order and share parameters, profiles and snippets (names
must not collide), e.g. `-p smoothmap.yaml -p diagnostics.yaml`. `apply`, `check` and `explain` lint
first and stop on any problem. As a library: `swf_patch.apply(data, paths, profile, overrides)`,
`swf_patch.check(...)`, `swf_patch.lint(paths)`, and `swf_patch.apply_fitting(...)`, which leaves out
each patch that doesn't apply (whole) instead of failing, and reports it. So a patch should be one
self-contained behaviour, never relying on another patch having applied.

## Open questions

* Whether `when` needs anything beyond booleans (e.g. comparisons on numbers).
* Whether to allow a Python escape hatch later, for genuinely procedural cases.
