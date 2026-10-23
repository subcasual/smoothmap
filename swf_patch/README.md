# swf_patch

Declarative patching of the ActionScript 3 bytecode inside Flash files: SWF, and Scaleform's GFX
(the UI format of many games). You describe changes in a YAML *patch set*; the engine finds the
code each change is anchored on, edits the bytecode directly (no decompiling and recompiling),
verifies the result, and writes a new file.

Patches are anchored on code, not on byte offsets: every pattern must match exactly where
expected, and every replaced constant must have its expected value. So a patch set written for
a game's file also applies to another mod's version of it when that mod left those spots alone,
and stops with a message naming the spot when it didn't.

Requires Python 3.9+ and PyYAML. The format is specified in [`patch-format.md`](patch-format.md).
For files wrapped in a game's own container, a small package adds the container and reuses
everything else here: [`redswf_patch`](../redswf_patch/README.md) does this for The Witcher 3's
`.redswf`.

## Command line

```
python -m swf_patch apply   -p A.yaml [-p B.yaml ...] [--profile P] [--set name=value ...] IN.swf OUT.swf
python -m swf_patch check   -p A.yaml [...] IN.swf     # dry run: for each patch, ok or why not
python -m swf_patch explain -p A.yaml [...]            # the active patches' title, why and as3, as Markdown
python -m swf_patch lint    A.yaml [B.yaml ...]        # structure, labels, locals, placeholders, snippets
```

`IN` is a SWF or GFX file, compressed or not. Several `-p` patch sets are applied in order and
share parameters, profiles and snippets. `apply`, `check` and `explain` lint first and stop on any
problem; nothing is written unless every patch applies.

## Library

```python
import swf_patch

with open("menu.gfx", "rb") as f:
    data = f.read()
out = swf_patch.apply(data, ["my_patches.yaml"], profile=None, overrides={"speed": 2})
report = swf_patch.check(data, ["my_patches.yaml"])     # [(patch id, "ok" | "FAILS: ..." | ...)]
problems = swf_patch.lint(["my_patches.yaml"])          # [] when clean
out, report = swf_patch.apply_fitting(data, ["my_patches.yaml"])   # leaves out patches that don't apply
left_out = swf_patch.failed(report)                     # [(patch id, why)]
```

Patch sets are linted first, as on the command line; a problem, or a patch that doesn't apply,
raises `swf_patch.PatchError`. `apply_fitting` is for files a patch set wasn't written for
(another mod's version): it leaves out each patch that doesn't apply, whole (nothing of a failed
patch stays), and reports it; when every patch applies, its output is exactly `apply`'s.

Lower-level pieces are usable on their own: `swf.read_abc`/`replace_abc` to get at the bytecode,
`abcfile.ABC` to read and edit it, `instructions.decode`/`encode` for method bodies,
`engine.patch_abc` to patch bytecode taken from any container, `listing.disassemble` for readable
code, and `cli.main` for a command line over another container's `apply` and `check`.

## How it works

```
apply(data, patch sets)                                   engine.py
  ├─ PatchSets: load YAML, parameters, conditions         patchset.py
  ├─ read_abc: the DoABC tag's bytecode                   swf.py
  ├─ ABC: constant pools, classes, methods                abcfile.py (bytestream.py)
  ├─ for each active patch, each operation:               operations.py
  │    ├─ Names: "HubMap.scrollMap" -> indices            names.py
  │    ├─ patterns: find the anchor in a method           patterns.py
  │    ├─ Assembler: assembly text -> instructions        assembler.py
  │    └─ MethodEditor: queue edits, then commit:         method_editor.py
  │         decode/encode the body                        instructions.py
  │         size and check stack and scope                verify.py
  └─ serialize the ABC, rebuild the file around it        swf.py
```

| Module | What it is |
|---|---|
| `engine.py` | `apply` and `check` on a SWF/GFX file; `patch_abc` and `check_abc` for bytecode from any container. |
| `patchset.py` | Patch set files, parameter values and profiles, `when` conditions, `{{placeholder}}` substitution, snippets. |
| `operations.py` | One function per operation (`set_initial_values`, `add_names`, `add_field`, `add_method`, `guard`, `capture`, `edit`). |
| `method_editor.py` | Editing one method body: insertions and replacements against the original code, then one commit. |
| `patterns.py` | Matching instruction patterns, with captures and `<jump-target>`. |
| `assembler.py` | Assembly text to instructions; the comment and label syntax shared with patterns and lint. |
| `names.py` | Resolving `Class`, `Class.member`, `package::name` to ABC indices; namespaces for new members. |
| `verify.py` | Stack and scope depth analysis of a method body (what the AVM2 verifier checks). |
| `instructions.py` | The AVM2 opcode table; decoding and encoding method bodies with labels instead of offsets. |
| `abcfile.py` | The ABC file model (pools, methods, classes, traits as dataclasses); lossless read/write. |
| `bytestream.py` | The ABC format's primitive encodings (variable-length integers etc.). |
| `swf.py` | The SWF/GFX file around the bytecode: header, compression, the DoABC tag. |
| `listing.py` | Readable views for tools: qualified class/method/field names, disassembly in patch syntax. |
| `lint.py` | Checking patch sets without applying them. |
| `cli.py`, `__main__.py` | The command line (`cli.py` is shared with containers like `redswf_patch`). |

Reading a file and writing it back unchanged reproduces it byte for byte, at every level
(payload, ABC, each method body); the tests check this on a real game file.

## Limits

* Only bytecode is edited: no other SWF tags (graphics, frames, symbols).
* One DoABC tag per file; LZMA-compressed SWFs (`ZWS`) aren't read.
* `add_method` adds methods, getters and setters (static or not, with parameters and defaults), but
  not methods using `arguments`, rest parameters or nested functions.
* Names in assembly must already exist in the file, or be added first: by `add_field` or
  `add_method`, or for other names by `add_names`. A disassembled method
  assembles back exactly unless it uses names private to a file, a second namespace set in its
  class, or the instructions assembly leaves out (see the spec's "Code"); in The Witcher 3's map
  file that's over 5000 of its 5713 methods, and the tests check every one.
* The verifier follows normal control flow, not exception handlers.

## Tests

From the repository root: `python -m unittest -v`. Tests that need The Witcher 3's file skip when
it isn't present.
