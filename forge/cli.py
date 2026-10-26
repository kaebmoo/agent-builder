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
from forge.pack_author import discover, scaffold_pack
from forge.pack_test import test_pack
from forge.spec import load_spec


def pack_command(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        if args.pack_command == "new":
            created = scaffold_pack(args.name, args.packs_dir)
            print(f"Scaffolded {args.packs_dir / args.name}: {', '.join(created)}")
            print("Not conformant yet: replace every TODO, implement scripts/server.py, then run forge pack test.")
            return 0
        if args.pack_command == "discover":
            draft = discover(args.name, args.server_command, args.args, args.env, timeout=args.timeout)
            if args.out is None:
                print(draft, end="")
            else:
                if args.out.exists() or args.out.is_symlink():
                    parser.exit(1, f"forge pack FAIL: refusing to overwrite {args.out}\n")
                args.out.write_text(draft, encoding="utf-8")
                print(f"Draft written to {args.out}; it is not conformant until the TODOs are filled and forge pack test passes.")
            return 0
        report, code = test_pack(args.name, args.packs_dir)
    except (ValueError, TypeError, OSError) as error:
        parser.exit(1, f"forge pack FAIL: {error}\n")
    print(f"pack {args.name}: {report['status']} ({['PASS', 'FAIL', 'SKIP'][code]}) {report['detail']}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(prog="forge")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("generate", help="generate an unverified Atlas or standalone package")
    build.add_argument("spec", type=Path)
    build.add_argument("--out", required=True, type=Path, help="new package directory; existing paths are refused")
    configure_audit(commands.add_parser("audit", help="static audit of a package; --write records the verdict"))
    configure_live(commands.add_parser("live-test", help="isolated golden-case live audit"))
    configure_export(commands.add_parser("export", help="Atlas registration export: register file, workflow, archive"))
    pack = commands.add_parser("pack", help="capability pack authoring and conformance")
    pack_commands = pack.add_subparsers(dest="pack_command", required=True)
    test = pack_commands.add_parser("test", help="test an external MCP directly without an LLM")
    test.add_argument("name")
    test.add_argument("--packs-dir", type=Path, default=ROOT / "packs", help="pack root (default: packs/)")
    new = pack_commands.add_parser("new", help="scaffold packs/<name>/ with a TODO-marked v2 descriptor")
    new.add_argument("name")
    new.add_argument("--packs-dir", type=Path, default=ROOT / "packs", help="pack root (default: packs/)")
    discover = pack_commands.add_parser("discover", help="draft pack.yaml from a running MCP server's tools/list")
    discover.add_argument("--name", required=True, help="server name; becomes the <server>__<tool> prefix")
    # dest differs from the top-level subcommand attribute `command`.
    discover.add_argument("--command", dest="server_command", required=True, help="server executable looked up on PATH")
    discover.add_argument("--env", action="append", default=[], metavar="NAME",
                          help="pass this env NAME through from the current process (repeatable); value is never printed")
    discover.add_argument("--timeout", type=float, default=10)
    discover.add_argument("--out", type=Path, help="write the draft here (new file) instead of stdout")
    discover.add_argument("--args", nargs=argparse.REMAINDER, default=[], help="literal server argv after --args")
    args = parser.parse_args()
    if args.command == "live-test":
        return live_command(args)
    if args.command == "export":
        return export_command(args)
    if args.command == "pack":
        return pack_command(parser, args)
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
