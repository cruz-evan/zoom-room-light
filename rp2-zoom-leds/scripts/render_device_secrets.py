#!/usr/bin/env python3
"""Render a MicroPython secrets.py file with selected environment overrides."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def render_secrets(source: str, overrides: dict[str, str]) -> str:
    output = source
    appended: list[str] = []

    for name, value in overrides.items():
        if not IDENTIFIER_RE.match(name):
            raise ValueError(f"Invalid secret name: {name}")

        assignment = f"{name} = {value!r}"
        pattern = re.compile(rf"^\s*{re.escape(name)}\s*=.*$", re.MULTILINE)
        output, count = pattern.subn(assignment, output, count=1)
        if count == 0:
            appended.append(assignment)

    if appended:
        if output and not output.endswith("\n"):
            output += "\n"
        output += "\n# Injected from environment during USB provisioning.\n"
        output += "\n".join(appended) + "\n"

    return output


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Source secrets.py/template path")
    parser.add_argument("--output", required=True, type=Path, help="Rendered output path")
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="NAME",
        help="Environment variable to copy into the rendered secrets.py",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    overrides = {}
    for name in args.env:
        if name not in os.environ:
            print(f"Environment variable is not set: {name}", file=sys.stderr)
            return 2
        overrides[name] = os.environ[name]

    source = args.input.read_text(encoding="utf-8")
    rendered = render_secrets(source, overrides)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
