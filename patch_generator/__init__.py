"""patch_generator: patch sets from edited ActionScript.

    import patch_generator
    patch_generator.export(gfx_bytes, ["HubMap"], "work")      # decompiles into work/; edit those files
    result = patch_generator.generate(gfx_bytes, "work")      # result.yaml: the patch set

Uses JPEXS FFDec (and Java) to decompile and compile, through ffdec_utils; built on swf_patch. README.md in this folder
has the workflow and the limits.
"""
from ffdec_utils import FFDec, FFDecError

from .errors import GeneratorError
from .workflow import Result, export, generate

__all__ = ["export", "generate", "FFDec", "FFDecError", "GeneratorError", "Result"]
