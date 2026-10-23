"""ffdec_utils: finding and running JPEXS FFDec (https://github.com/jindrapetrik/jpexs-decompiler),
which decompiles classes of a SWF or GFX file to ActionScript and compiles ActionScript back. FFDec
itself isn't included: install it (and Java).

    ffdec = FFDec()                                         # found as below
    ffdec.decompile("in.swf", ["pkg.Class"], "out")        # out/scripts/pkg/Class.as
    ffdec.compile("in.swf", "out.swf", {"pkg.Class": "Class.as"})

FFDec runs one of two ways:

    command line  ffdec-cli.exe / ffdec.bat / ffdec.sh, or ffdec.jar run with Java, from a full FFDec
                  install: decompiles and compiles.
    library       ffdec_lib.jar (the "FFDec library" ZIP, or a full install's lib folder) through the
                  small helper in java/, with Java 8+ (no JDK needed): decompiles only. With
                  FFDec(library=True), decompiling uses it where it's found.

Both decompile to the same files. FFDec is looked for at an explicit path (a folder, or one of the
files above), the FFDEC environment variable, the default Windows install folders, then PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess

DEFAULT_LOCATIONS = [r"C:\Program Files (x86)\FFDec", r"C:\Program Files\FFDec"]
COMMAND_NAMES = ["ffdec-cli.exe", "ffdec.bat", "ffdec.sh", "ffdec"]
JAVA_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "java")   # FFDecExport.class

__all__ = ["FFDec", "FFDecError", "find_command", "find_library", "find_java", "locations"]


class FFDecError(Exception):
    """FFDec (or Java) isn't installed, or FFDec failed."""


class FFDec:
    def __init__(self, path: str | None = None, library: bool = False) -> None:
        """FFDec at path, or wherever it's found. library: decompile with FFDec's library where one
        is found (compiling still needs the command line, looked for when first needed)."""
        self.path = path
        self.library = find_library(path) if library else None
        self._command = None if self.library else find_command(path)

    @property
    def command(self) -> list[str]:
        if self._command is None:
            self._command = find_command(self.path)
        return self._command

    def decompile(self, swf_path: str, classes: list[str], out_dir: str) -> None:
        """Writes <out_dir>/scripts/<package path>/<Class>.as for each fully qualified class name
        (package.Class)."""
        if self.library:
            classpath = os.pathsep.join([os.path.join(self.library, "*"), JAVA_HELPER])
            self._run([find_java(), "-Xmx2g", "-cp", classpath, "FFDecExport", swf_path,
                       os.path.join(out_dir, "scripts"), ",".join(classes)], "decompiling")
        else:
            self._run(self.command + ["-format", "script:as", "-selectclass", ",".join(classes),
                                      "-export", "script", out_dir, swf_path], "decompiling")

    def compile(self, swf_path: str, out_path: str, sources: dict[str, str]) -> None:
        """Writes the file with each class (package.Class) compiled from its ActionScript file."""
        args = ["-replace", swf_path, out_path]
        for class_name, source in sources.items():
            args += [class_name, source]
        self._run(self.command + args, "compiling")

    def _run(self, command: list[str], doing: str) -> None:
        try:
            result = subprocess.run(command, capture_output=True, text=True, errors="replace")
        except OSError as e:
            raise FFDecError(f"couldn't run FFDec ({' '.join(command)}): {e}")
        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip().splitlines()
            errors = [line for line in output if "SEVERE" in line or "rror" in line] or output[-10:]
            raise FFDecError(f"FFDec failed {doing}:\n  " + "\n  ".join(errors))


def locations(explicit: str | None = None) -> list[str]:
    """Where to look for FFDec, in order: the explicit path, the FFDEC environment variable, the
    default install folders."""
    return [c for c in (explicit, os.environ.get("FFDEC"), *DEFAULT_LOCATIONS) if c]


def find_command(explicit: str | None = None) -> list[str]:
    """The command line that runs FFDec (found in locations(), else on PATH)."""
    for candidate in locations(explicit):
        if os.path.isfile(candidate):
            return _command_for(candidate)
        for name in COMMAND_NAMES + ["ffdec.jar"]:
            path = os.path.join(candidate, name)
            if os.path.isfile(path):
                return _command_for(path)
    for name in COMMAND_NAMES:
        found = shutil.which(name)
        if found:
            return [found]
    raise FFDecError("FFDec not found: install it (https://github.com/jindrapetrik/jpexs-decompiler), "
                     "or pass its folder, or set the FFDEC environment variable")


def find_library(explicit: str | None = None) -> str | None:
    """The first folder in locations() holding ffdec_lib.jar (it, or its lib folder), or None."""
    for candidate in locations(explicit):
        if os.path.isfile(candidate) and os.path.basename(candidate).lower() == "ffdec_lib.jar":
            return os.path.dirname(candidate)
        for folder in (candidate, os.path.join(candidate, "lib")):
            if os.path.isdir(folder) and os.path.exists(os.path.join(folder, "ffdec_lib.jar")):
                return folder
    return None


def find_java() -> str:
    """Java: JAVA_HOME's, else the one on PATH."""
    for candidate in (os.path.join(os.environ.get("JAVA_HOME", ""), "bin", "java"), shutil.which("java")):
        if candidate and (os.path.isfile(candidate) or os.path.isfile(candidate + ".exe")):
            return candidate
    raise FFDecError("Java not found (FFDec needs it): install a Java 8+ runtime, or set JAVA_HOME")


def _command_for(path: str) -> list[str]:
    return [find_java(), "-jar", path] if path.lower().endswith(".jar") else [path]
