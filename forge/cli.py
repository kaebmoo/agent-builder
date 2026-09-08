"""Small command-line entry point for deterministic package generation and static audit."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from jinja2 import TemplateError
from jsonschema import ValidationError

from forge.atlas_export import command as export_command
from forge.atlas_export import configure as configure_export
from forge.audit import command as audit_command
from forge.audit import configure as configure_audit
from forge.generate import ROOT, generate
from forge.live_test import command as live_command
from forge.live_test import configure as configure_live
from forge.pack_test import test_pack
from forge.spec import load_spec


def main() -> int:
    parser = argparse.ArgumentParser(prog="forge")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("generate", help="generate an unverified Atlas single-worker package")
    build.add_argument("spec", type=Path)
    build.add_argument("--out", required=True, type=Path, help="new package directory; existing paths are refused")
    configure_audit(commands.add_parser("audit", help="static audit of a package; --write records the verdict"))
    configure_live(commands.add_parser("live-test", help="isolated golden-case live audit"))
    configure_export(commands.add_parser("export", help="Atlas registration export: register file, workflow, archive"))
    pack = commands.add_parser("pack", help="capability pack conformance")
    pack_commands = pack.add_subparsers(dest="pack_command", required=True)
    test = pack_commands.add_parser("test", help="test an external MCP directly without an LLM")
    test.add_argument("name")
    args = parser.parse_args()
    if args.command == "live-test":
        return live_command(args)
    if args.command == "export":
        return export_command(args)
    if args.command == "pack":
        try:
            report, code = test_pack(args.name, ROOT / "packs")
        except (ValueError, OSError) as error:
            parser.exit(1, f"forge pack FAIL: {error}\n")
        print(f"pack {args.name}: {report['status']} ({['PASS', 'FAIL', 'SKIP'][code]}) {report['detail']}")
        return code
    if args.command == "audit":
        return audit_command(args)
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
