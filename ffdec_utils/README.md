# ffdec_utils

Finding and running [JPEXS FFDec](https://github.com/jindrapetrik/jpexs-decompiler), which decompiles
classes of a SWF or GFX file to ActionScript and compiles ActionScript back. FFDec itself isn't
included: install it, and Java. Python 3.9+, no dependencies.

```python
from ffdec_utils import FFDec, FFDecError

ffdec = FFDec()                                                  # or FFDec(path), FFDec(library=True)
ffdec.decompile("in.swf", ["pkg.Class"], "out")                  # writes out/scripts/pkg/Class.as
ffdec.compile("in.swf", "out.swf", {"pkg.Class": "Class.as"})    # the file with pkg.Class compiled from Class.as
```

FFDec runs one of two ways, and both decompile to the same files:

| Way | What it uses | Does |
|---|---|---|
| command line | `ffdec-cli.exe`, `ffdec.bat`, `ffdec.sh`, or `ffdec.jar` run with Java, from a full FFDec install | decompile, compile |
| library | `ffdec_lib.jar` (the "FFDec library" ZIP, or a full install's `lib` folder), through the small helper in `java/` with Java 8+ (no JDK needed) | decompile |

`FFDec(library=True)` decompiles with the library where one is found, and looks for the command
line only when it's needed (to compile, or when there's no library).

FFDec is looked for at an explicit path (a folder, or one of the files above), the `FFDEC`
environment variable, the default Windows install folders, then PATH; Java at `JAVA_HOME`, then
PATH. Anything missing, or FFDec failing, raises `FFDecError`.

Used by [`patch_generator`](../patch_generator/README.md) and `tools/as3diff.py`.
