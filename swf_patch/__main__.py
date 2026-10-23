"""Command line: python -m swf_patch <command> ... on SWF or GFX files (see cli.py), e.g.

    python -m swf_patch apply -p my_patches.yaml IN.swf OUT.swf
"""
import sys

from .cli import main
from .engine import apply, check

if __name__ == "__main__":
    sys.exit(main("python -m swf_patch", apply, check))
