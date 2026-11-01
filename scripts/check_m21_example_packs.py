#!/usr/bin/env python3
"""M21 gate: independent example packs (sqlite-readonly, csv-readonly, datetime-tools) conform, generate, audit and stay read-only.

Exit 0 = offline and native checks passed; 1 = failure; 2 = offline passed but thclaws is absent.
Live evidence (candidate/shippable) is outside this gate's scope. No provider key is used.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

try:
    import yaml
    from jsonschema import Draft202012Validator

    from forge.audit import audit_package, write_report
    from forge.generate import REPORT_SCHEMA_PATH, THCLAWS_BASELINE, generate
    from forge.pack_test import RPC, setup_fixture, test_pack
    from forge.packs import read_pack
    from forge.security import security_audit
    from forge.spec import load_spec
except ModuleNotFoundError:
    print('M21 FAIL: install project dependencies from pyproject.toml first')
    raise SystemExit(1)

PACKS = {'sqlite-readonly': 'sqlite-reader', 'csv-readonly': 'csv-reader',
         'datetime-tools': 'datetime-helper'}
IGNORE = shutil.ignore_patterns('pack-conformance.json', '__pycache__')
VALIDATOR = Draft202012Validator(json.loads(REPORT_SCHEMA_PATH.read_text(encoding='utf-8')))


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in sorted(folder.rglob('*')) if p.is_file()}


def negative_descriptors(base, packs):
    for label, needle in (('smoke-only', 'smoke and negative'),
                          ('mutating-t0', 'mutating tools require min_tier T2')):
        folder = base / 'packs_bad' / label / 'sqlite-readonly'
        shutil.copytree(packs / 'sqlite-readonly', folder, ignore=IGNORE)
        pack = read_pack(folder)
        if label == 'smoke-only':
            pack['fixture']['cases'] = [case for case in pack['fixture']['cases'] if case['expect'] == 'ok']
        else:
            pack['mcp_servers'][0]['mutating'] = ['query']
            assert pack['min_tier'] == 'T0', pack
        (folder / 'pack.yaml').write_text(yaml.safe_dump(pack, sort_keys=False), encoding='utf-8')
        try:
            read_pack(folder)
        except ValueError as exc:
            assert needle in str(exc), str(exc)
        else:
            raise AssertionError(f'read_pack accepted {label}')


def call(rpc, tool, arguments, *, error=False, needle=''):
    result = rpc.call('tools/call', {'name': tool, 'arguments': arguments})
    assert result.get('isError', False) is error, (tool, result)
    text = result['content'][0]['text']
    if error:
        assert text and needle in text, (tool, result)
        return None
    return json.loads(text)


def probes(base, packs, name, timeout):
    folder = packs / name
    pack = read_pack(folder)
    work = base / f'work-{name}'
    work.mkdir()
    env_values, _ = setup_fixture(folder, pack, work, timeout)
    rpc = RPC([sys.executable, str(folder / 'scripts/server.py')], cwd=work,
              env={'PATH': os.environ.get('PATH', os.defpath), **env_values}, timeout=timeout)
    try:
        initialized = rpc.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                             'clientInfo': {'name': 'forge-m21', 'version': '0.1'}})
        assert initialized['protocolVersion'] == '2024-11-05', initialized
        rpc.send({'method': 'notifications/initialized'})
        if name == 'sqlite-readonly':
            call(rpc, 'query', {'sql': 'UPDATE invoices SET total = 0'},
                 error=True, needle='only SELECT/WITH')
            call(rpc, 'query', {'sql': 'WITH t AS (DELETE FROM invoices RETURNING *) SELECT * FROM t'},
                 error=True)
            tables = call(rpc, 'tables', {})
            invoices = next(table for table in tables['tables'] if table['name'] == 'invoices')
            assert invoices['rows'] == 6, tables
        elif name == 'csv-readonly':
            call(rpc, 'columns', {'file': '../outside.csv'}, error=True)
            columns = call(rpc, 'columns', {'file': 'sales.csv'})
            assert columns['row_count'] == 8, columns
            totals = call(rpc, 'aggregate', {'file': 'sales.csv', 'metric': 'sum',
                                            'column': 'amount', 'group_by': 'region'})
            assert totals['groups'] == [{'key': 'east', 'value': 250.0},
                                        {'key': 'north', 'value': 310.0},
                                        {'key': 'south', 'value': 139.5}], totals
        else:
            args = {'datetime': '2026-01-01T09:00:00',
                    'from_timezone': 'Asia/Bangkok', 'to_timezone': 'UTC'}
            converted = call(rpc, 'convert', args)
            assert converted['iso'] == '2026-01-01T02:00:00+00:00', converted
            added = call(rpc, 'add_business_days', {'date': '2026-10-30', 'days': 2})
            assert added['date'] == '2026-11-03', added
            call(rpc, 'convert', {**args, 'to_timezone': 'Mars/Olympus'},
                 error=True, needle='unknown timezone')
    finally:
        rpc.close()


def offline(base, packs, timeout):
    for name in PACKS:
        shutil.copytree(ROOT / 'packs' / name, packs / name, ignore=IGNORE)
        evidence, code = test_pack(name, packs, timeout=timeout)
        assert code == 0 and evidence['status'] == 'conformant', (name, code, evidence)
    negative_descriptors(base, packs)

    packages = {}
    for name, agent in PACKS.items():
        spec = load_spec(ROOT / f'fixtures/{agent}/spec.yaml')
        package = base / agent
        generated = generate(spec, package, packs_dir=packs)
        repeated = base / f'{agent}-repeat'
        generate(spec, repeated, packs_dir=packs)
        assert snapshot(package) == snapshot(repeated), f'{agent}: generation is not byte deterministic'
        config = json.loads((package / '.thclaws/mcp.json').read_text(encoding='utf-8'))
        assert config == {'mcpServers': {name: {
            'command': 'python3', 'args': [f'.thclaws/scripts/{name}--server.py']}}}, config
        manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest['requires']['mcp_servers'] == [name], manifest
        assert (package / f'.thclaws/scripts/{name}--server.py').read_bytes() == (
            ROOT / 'packs' / name / 'scripts/server.py').read_bytes(), f'{name}: bundled script differs'
        dependencies = generated['dependencies']
        assert len(dependencies) == 1, dependencies
        assert dependencies[0]['pack'] == name and dependencies[0]['status'] == 'conformant', dependencies

        report = audit_package(package, packs_dir=packs, strict=True, thclaws=False)
        assert report['static_audit']['findings'] == [], report['static_audit']
        assert report['audit']['static'] == 'passed' and report['package_status'] == 'unverified', report
        security_audit(package, report, packs_dir=packs)
        assert report['security_audit']['status'] == 'passed', report['security_audit']
        assert report['audit']['security'] == 'passed' and report['package_status'] == 'unverified', report
        VALIDATOR.validate(report)
        probes(base, packs, name, timeout)
        packages[agent] = package
    return packages


def binary_pair(binary):
    version = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True, timeout=30)
    print(version.stdout.strip())
    lines = [line.strip() for line in version.stdout.splitlines() if line.strip()]
    return lines[0].removeprefix('thclaws ') if lines else ''


def native(packages, packs, binary):
    version = binary_pair(binary)
    assert version == THCLAWS_BASELINE, (
        f'thclaws {version} is not the {THCLAWS_BASELINE} baseline; re-check DESIGN §4 first')
    for agent, package in packages.items():
        with patch.dict(os.environ, {'THCLAWS_BIN': binary}):
            report = audit_package(package, packs_dir=packs, strict=True)
        assert report['package_status'] == 'draft' and report['audit']['manifest'] == 'passed', report
        security_audit(package, report, packs_dir=packs)
        assert report['security_audit']['status'] == 'passed', report['security_audit']
        assert report['audit']['security'] == 'passed' and report['package_status'] == 'draft', report
        write_report(package, report)
        stored = json.loads((package / 'builder-build-report.json').read_text(encoding='utf-8'))
        VALIDATOR.validate(stored)
        assert stored == report, f'{agent}: written report differs'
        print(f'M21 native PASS: {agent} draft on {report["static_audit"]["thclaws_validate"]["version"]}')
    return 0


def unchanged(before):
    if snapshot(ROOT / 'packs') != before:
        raise AssertionError('gate changed the checkout packs tree')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=30,
                        help='seconds per pack test/probe (default: 30)')
    args = parser.parse_args()
    try:
        before = snapshot(ROOT / 'packs')
        try:
            with tempfile.TemporaryDirectory(prefix='forge-m21-') as directory:
                base = Path(directory).resolve()
                with patch.object(tempfile, 'tempdir', str(base)), patch.dict(os.environ, {
                    'TMPDIR': str(base), 'PYTHONDONTWRITEBYTECODE': '1',
                    'PYTHONPYCACHEPREFIX': str(base / 'pycache'),
                }):
                    packs = base / 'packs'
                    packages = offline(base, packs, args.timeout)
                    unchanged(before)
                    print('M21 offline PASS: 3 packs conformant, negative descriptors rejected, '
                          '3 example packages deterministic/strict-clean/security-passed, read-only probes held')
                    binary = shutil.which(os.environ.get('THCLAWS_BIN', 'thclaws'))
                    if not binary:
                        print('M21 SKIP: thclaws absent; native validation of the example packages '
                              'not run (exit 2)')
                        code = 2
                    else:
                        code = native(packages, packs, binary)
                    print('M21 scope: live evidence (candidate/shippable) is out of this gate\'s scope')
                    return code
        finally:
            unchanged(before)
    except (AssertionError, ValueError, OSError, StopIteration, subprocess.SubprocessError) as exc:
        print(f'M21 FAIL: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
