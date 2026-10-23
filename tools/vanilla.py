"""The vanilla file: REDkit's depot copy of build.yaml's resource, at redkit.depot. The tools accept
`vanilla` wherever they take a file, and the release builds from it (target input `vanilla`)."""
from __future__ import annotations

import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VANILLA = "vanilla"


def vanilla_path(cfg: dict | None = None) -> str:
    """The depot copy of the resource. Raises LookupError if it isn't there."""
    if cfg is None:
        with open(os.path.join(ROOT, "build.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    depot = (cfg.get("redkit") or {}).get("depot") or ""
    path = os.path.join(depot, *cfg["resource"].split("\\"))
    if not os.path.isfile(path):
        raise LookupError(f"no {cfg['resource']} in REDkit's depot ({depot or 'redkit.depot not set'}): "
                          "set redkit.depot in build.yaml to REDkit's r4data folder")
    return path


def resolve(argument: str) -> str:
    """A tool's file argument: `vanilla` means the depot copy; anything else is itself."""
    if argument != VANILLA:
        return argument
    try:
        return vanilla_path()
    except LookupError as e:
        raise SystemExit(f"error: {e}")
