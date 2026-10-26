#!/usr/bin/env python3
"""Deterministic build runner for the builder workspace (M11, DESIGN §9).

Refuses a spec whose permissions/env/capabilities were not confirmed by a person (`confirmed_by`), strips that
marker (forge does not know it), tests every declared pack that has a descriptor, generates the package and
records the static audit. Every status line comes from forge; nothing here is a model judgement.

Exit 0 = package is draft; 1 = refused or a layer failed; 2 = thclaws absent, package stays unverified.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

CONFIRMED_BLOCKS = ('permissions', 'env', 'capabilities')
RESERVED_CONFIRMERS = {'model', 'assistant', 'agent', 'builder', 'builder-agent', 'llm', 'ai', 'claude', 'gpt',
                       'thclaws', 'todo', 'tbd', 'none', 'unknown', 'n/a', 'pending'}


def builder_root() -> Path:
    """The agent-builder checkout: AGENT_BUILDER_ROOT, or the checkout this workspace lives in."""
    candidate = Path(os.environ.get('AGENT_BUILDER_ROOT') or Path(__file__).resolve().parents[3])
    if not (candidate / 'forge' / 'cli.py').is_file():
        raise SystemExit(f'FAILED: agent-builder checkout not found at {candidate}; set AGENT_BUILDER_ROOT')
    return candidate.resolve()


def refuse(reason: str) -> None:
    print(f'REFUSED: {reason}')
    print('Ask the person to review permissions, env and capabilities, then record confirmed_by as the spec-author skill describes.')
    raise SystemExit(1)


def confirmed_spec(path: Path) -> dict:
    import yaml

    try:
        spec = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise SystemExit(f'FAILED: cannot read {path}: {type(exc).__name__}') from None
    if not isinstance(spec, dict):
        raise SystemExit('FAILED: spec.yaml must be a mapping')
    marker = spec.get('confirmed_by')
    if not isinstance(marker, dict):
        refuse('spec.yaml has no confirmed_by block; permissions, env and capabilities must be confirmed by a person')
    if set(marker) != set(CONFIRMED_BLOCKS):
        refuse(f'confirmed_by must name exactly {list(CONFIRMED_BLOCKS)} (network is confirmed with permissions); '
               f'found {sorted(marker)}')
    for block in CONFIRMED_BLOCKS:
        who = marker[block]
        if not isinstance(who, str) or not who.strip() or who.strip().casefold() in RESERVED_CONFIRMERS:
            refuse(f'confirmed_by.{block} must be the name of the person who confirmed it (got {who!r})')
        if block not in spec:
            refuse(f'confirmed_by.{block} is present but the spec has no {block} block')
    return {key: value for key, value in spec.items() if key != 'confirmed_by'}


def main() -> int:
    parser = argparse.ArgumentParser(prog='build.py', description=__doc__.split('\n\n')[0])
    parser.add_argument('spec', type=Path, help='work/<name>/spec.yaml with a confirmed_by block')
    parser.add_argument('--out', required=True, type=Path, help='new package directory, usually out/<name>')
    parser.add_argument('--replace', action='store_true', help='remove a package build.py produced earlier at --out')
    args = parser.parse_args()
    root = builder_root()
    sys.path.insert(0, str(root))
    from forge.audit import audit_package, exit_code, write_report
    from forge.generate import generate
    from forge.pack_test import test_pack
    from forge.spec import validate_spec

    packs_dir = Path(os.environ.get('AGENT_BUILDER_PACKS') or root / 'packs').resolve()
    spec = confirmed_spec(args.spec)
    try:
        validate_spec(spec)
    except (ValueError, TypeError) as exc:
        print(f'FAILED: AgentSpec validation: {exc}')
        return 1
    out = args.out
    if out.exists() or out.is_symlink():
        generated = (out / 'builder-build-report.json').is_file() and (out / 'agentspec.json').is_file()
        if not (args.replace and generated and not out.is_symlink()):
            print(f'FAILED: {out} already exists; pass --replace only for a package build.py generated earlier')
            return 1
        shutil.rmtree(out)

    pack_tests = {}
    for capability in spec['capabilities']:
        name = capability['pack']
        if (packs_dir / name / 'pack.yaml').is_file():
            try:
                evidence, code = test_pack(name, packs_dir)
                pack_tests[name] = f"forge pack test: {['PASS', 'FAIL', 'SKIP'][code]} {evidence['detail']}".rstrip()
            except (ValueError, TypeError, OSError) as exc:
                pack_tests[name] = f'forge pack test: FAIL {exc}'
        else:
            pack_tests[name] = f'no descriptor under {packs_dir}; implement it separately (pack-author) and rerun'

    try:
        generate(spec, out, packs_dir=packs_dir)
    except (ValueError, TypeError, OSError) as exc:
        print(f'FAILED: forge generate: {exc}')
        return 1
    with tempfile.TemporaryDirectory(prefix='builder-pycache-') as cache:
        os.environ.setdefault('PYTHONPYCACHEPREFIX', cache)
        report = audit_package(out, packs_dir=packs_dir)
    write_report(out, report)

    print(f"build: {spec['identity']['name']} -> {out}")
    print(f"agent-builder checkout: {root}; packs: {packs_dir}")
    print(f"package_status={report['package_status']}")
    print('audit: ' + ' '.join(f'{layer}={status}' for layer, status in report['audit'].items()))
    for dependency in report['dependencies']:
        print(f"pack {dependency['pack']}: {dependency['status']} ({pack_tests.get(dependency['pack'], '')})")
    for finding in report['static_audit']['findings']:
        print(f"finding {finding['severity']} {finding['rule']}: {finding['message']}")
    validate = report['static_audit']['thclaws_validate']
    print(f"thclaws agent validate: {validate['status']} {validate['detail']}".rstrip())
    code = exit_code(report)
    print(f"RESULT: {['PASS', 'FAIL', 'SKIP'][code]} (exit {code}); builder-build-report.json is the only record of this verdict")
    return code


if __name__ == '__main__':
    raise SystemExit(main())
