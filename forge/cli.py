"""Small command-line entry point for deterministic package generation."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from jinja2 import TemplateError
from jsonschema import ValidationError

from forge.generate import generate
from forge.spec import load_spec


def main() -> int:
    parser = argparse.ArgumentParser(prog="forge")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("generate", help="generate an unverified Atlas single-worker package")
    build.add_argument("spec", type=Path)
    build.add_argument("--out", required=True, type=Path, help="new package directory; existing paths are refused")
    args = parser.parse_args()
    try:
        report = generate(load_spec(args.spec), args.out)
    except (OSError, ValueError, TypeError, yaml.YAMLError, TemplateError, ValidationError) as error:
        parser.exit(1, f"forge FAIL: {error}\n")
    print(f"Generated {len(report['generated_files'])} files in {args.out}; package_status=unverified")
    print("Manifest, static, live and security audits have not run.")
    for dependency in report["dependencies"]:
        if dependency["status"] == "missing":
            print(f"UNRESOLVED pack: {dependency['pack']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
