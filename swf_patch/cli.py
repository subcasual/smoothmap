"""The command line, shared by swf_patch (SWF/GFX files) and redswf_patch (.redswf files): each
passes in its own apply and check.

    apply   -p A.yaml [-p B.yaml ...] [--profile P] [--set name=value ...] IN OUT
    check   -p A.yaml [-p B.yaml ...] [--profile P] [--set name=value ...] IN
    explain -p A.yaml [-p B.yaml ...] [--profile P] [--set name=value ...]
    lint    A.yaml [B.yaml ...]

Patch sets given with several -p are applied in that order and share parameters. apply, check
and explain lint the patch sets first and stop on any problem.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Callable, Optional

import yaml

from .errors import PatchError
from .lint import lint
from .patchset import PatchSets

EXIT_OK, EXIT_PROBLEMS, EXIT_ERROR = 0, 1, 2
COMMANDS = ("apply", "check", "explain")

# (file bytes, patch set paths, profile, overrides) -> the patched file / the check report
Apply = Callable[[bytes, list[str], Optional[str], dict[str, Any]], bytes]
Check = Callable[[bytes, list[str], Optional[str], dict[str, Any]], list[tuple[str, str]]]


def main(prog: str, apply: Apply, check: Check, argv: list[str] | None = None) -> int:
    args = _parser(prog).parse_args(argv)
    try:
        if args.command == "lint":
            problems = lint(args.files)
            _print_problems(problems, sys.stdout)
            print(f"{len(problems)} problem(s)")
            return EXIT_PROBLEMS if problems else EXIT_OK
        problems = lint(args.patches)
        if problems:
            _print_problems(problems, sys.stderr)
            raise PatchError(f"{len(problems)} problem(s) in the patch set(s); nothing was done")
        overrides = parse_overrides(args.set)
        if args.command == "apply":
            return _apply(args, overrides, apply)
        if args.command == "check":
            return _check(args, overrides, check)
        return _explain(args, overrides)
    except PatchError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR


def _apply(args: argparse.Namespace, overrides: dict[str, Any], apply: Apply) -> int:
    with open(args.input, "rb") as f:
        out = apply(f.read(), args.patches, args.profile, overrides)
    with open(args.output, "wb") as f:
        f.write(out)
    print(f"patched -> {args.output}")
    return EXIT_OK


def _check(args: argparse.Namespace, overrides: dict[str, Any], check: Check) -> int:
    with open(args.input, "rb") as f:
        report = check(f.read(), args.patches, args.profile, overrides)
    for patch_id, status in report:
        print(f"  {patch_id:34} {status}")
    return EXIT_PROBLEMS if any(status.startswith("FAILS") for _, status in report) else EXIT_OK


def _explain(args: argparse.Namespace, overrides: dict[str, Any]) -> int:
    """The active patches' documentation as Markdown (title, why, ActionScript), e.g. for a changelog."""
    sets = PatchSets(args.patches)
    for patch in sets.active(sets.values(args.profile, overrides)):
        print(f"## {patch.title or patch.id}\n")
        if patch.why:
            print(patch.why.strip() + "\n")
        if patch.as3:
            print("```actionscript\n" + patch.as3.rstrip() + "\n```\n")
    return EXIT_OK


def parse_overrides(pairs: list[str] | None) -> dict[str, Any]:
    """--set name=value pairs -> {name: value}, values parsed as YAML (0.3 -> float, false -> bool)."""
    overrides = {}
    for pair in pairs or []:
        name, sep, value = pair.partition("=")
        if not sep:
            raise PatchError(f"--set needs name=value, got '{pair}'")
        overrides[name.strip()] = yaml.safe_load(value)
    return overrides


def _print_problems(problems: list[str], stream: Any) -> None:
    for problem in problems:
        print("ERROR", problem, file=stream)


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Apply, check, explain or lint patch sets.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = commands.add_parser(name)
        command.add_argument("-p", "--patches", action="append", required=True, metavar="PATCHSET.yaml",
                             help="a patch set; repeat to apply several in order")
        command.add_argument("--profile")
        command.add_argument("--set", action="append", metavar="NAME=VALUE")
        if name in ("apply", "check"):
            command.add_argument("input")
        if name == "apply":
            command.add_argument("output")
    commands.add_parser("lint").add_argument("files", nargs="+")
    return parser
