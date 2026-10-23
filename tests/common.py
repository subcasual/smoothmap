"""What the tests share: the patch sets, the game's file they run on, and FFDec.

The game's file is REDkit's depot copy of build.yaml's resource (redkit.depot), and must be 5.01's.
Tests that need it, or FFDec, skip without them.
"""
import functools
import hashlib
import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from ffdec_utils import FFDec, FFDecError                       # noqa: E402

SMOOTHMAP = os.path.join(ROOT, "patches", "smoothmap.yaml")
DIAGNOSTICS = os.path.join(ROOT, "patches", "diagnostics.yaml")
VANILLA_SHA256 = "628d81120c14fca9abd5e0ab21a793e88c1fa56e8244171e2fd6b6e041b66ba5"   # 5.01's depot file
NO_VANILLA = "REDkit's depot copy of panel_worldmap.redswf (5.01) not found: set redkit.depot in build.yaml"
NO_FFDEC = "FFDec not installed"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _vanilla_path():
    with open(os.path.join(ROOT, "build.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return os.path.join((cfg.get("redkit") or {}).get("depot") or "", *cfg["resource"].split("\\"))


VANILLA = _vanilla_path()


@functools.lru_cache(maxsize=None)
def have_vanilla():
    return os.path.isfile(VANILLA) and sha256(read_bytes(VANILLA)) == VANILLA_SHA256


def have_ffdec(library=False):
    """Whether FFDec is installed: its command line, or with library, its library or command line."""
    try:
        FFDec(library=library)
        return True
    except FFDecError:
        return False
