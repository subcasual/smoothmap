"""Command line: python -m redswf_patch <command> ... on .redswf files (see swf_patch/cli.py), e.g.

    python -m redswf_patch apply -p my_patches.yaml IN.redswf OUT.redswf
"""
import sys

from swf_patch.cli import main

from .engine import apply, check

if __name__ == "__main__":
    sys.exit(main("python -m redswf_patch", apply, check))
