"""The three versions of an edited class the generator compares: the original file's, and FFDec's
compiles of the exported and of the edited source. Comparing the two compiles gives the edit;
the original is what the patches change."""
from __future__ import annotations

from dataclasses import dataclass

from swf_patch.abcfile import ABC
from swf_patch.listing import disassemble
from swf_patch.names import Names

from .reading import methods


@dataclass
class Version:
    abc: ABC
    names: Names
    cls: int                       # the class's index in abc
    methods: dict[str, int]        # member (as tools name it: name, get name, <constructor>...) -> method index

    @classmethod
    def of(cls, abc: ABC, names: Names, class_index: int) -> Version:
        return cls(abc, names, class_index, methods(abc, class_index))

    def code(self, member: str) -> list[str]:
        """A member's disassembly, without its header line."""
        return disassemble(self.abc, self.methods[member], names=self.names)[1:]


@dataclass
class ClassVersions:
    original: Version              # the file the patches are for
    exported: Version              # FFDec's compile of the exported source
    edited: Version                # FFDec's compile of the edited source
    ref: str                       # the class as patch sets write it: its short name if unique, else package::Class
    short: str                     # its short name, for messages and finding it in the source
    exported_text: str             # the class's source as exported
    edited_text: str               # ... and as edited

    @property
    def all(self) -> tuple[Version, Version, Version]:
        return self.original, self.exported, self.edited
